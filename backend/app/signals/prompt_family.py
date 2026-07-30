"""Prompt v7 family routing, deterministic composition, metadata and contracts.

This module is deliberately dependency-light so cron, API, replay and tests all
resolve the same prompt family without importing an LLM client.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse


PROMPT_FAMILY_VERSION = "v7"
LEGACY_PROMPT_FAMILY = "legacy_split"
SHARED_POLICY_VERSION = "v7"
TRACKING_STATE_MACHINE_VERSION = "p4_state_v1"
RESPONSE_CONTRACT_VERSIONS = {
    "research": "v7_research_json_schema_v1",
    "assessment": "v7_assessment_json_schema_v1",
    "global_selector": "v7_global_selector_json_schema_v1",
    "reason": "v7_reason_json_schema_v1",
    "tracking": "v7_tracking_json_schema_v1",
}

STAGE_VERSIONS: Dict[str, Dict[str, str]] = {
    PROMPT_FAMILY_VERSION: {
        "research": "v7_research",
        "assessment": "v7_assessment",
        "global_selector": "v7_global_selector",
        "reason": "v7_reason",
        "tracking": "v7_tracking",
    },
    LEGACY_PROMPT_FAMILY: {
        "research": "v6.1",
        "assessment": "p3_assessment_v1",
        "global_selector": "p3_global_v1",
        "reason": "p3_reason_v1",
        "tracking": "p4_tracking_v1",
    },
}

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
_V7_FILES = {
    "shared": "shared-policy-v7.md",
    "research": "candidate-research-v7.md",
    "assessment": "candidate-assessment-v7.md",
    "global_selector": "global-recommendation-selector-v7.md",
    "reason": "recommendation-reason-v7.md",
    "tracking": "tracking-review-v7.md",
}
_LEGACY_STAGE_FILES = {
    "global_selector": "global-recommendation-selector-v1.md",
    "tracking": "tracking-review-v1.md",
}


class PromptFamilyError(RuntimeError):
    """Fail-closed prompt configuration or stage contract error."""


def resolve_prompt_family(explicit: Optional[str] = None) -> str:
    family = (explicit or os.getenv("SIGNALS_PROMPT_FAMILY", PROMPT_FAMILY_VERSION)).strip()
    if family not in STAGE_VERSIONS:
        raise PromptFamilyError(
            f"Unknown SIGNALS_PROMPT_FAMILY={family!r}; expected v7 or legacy_split."
        )
    return family


def stage_version(stage: str, family: Optional[str] = None) -> str:
    resolved = resolve_prompt_family(family)
    try:
        return STAGE_VERSIONS[resolved][stage]
    except KeyError as exc:
        raise PromptFamilyError(f"Unknown prompt stage: {stage!r}") from exc


@lru_cache(maxsize=16)
def _read_prompt_file(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


@lru_cache(maxsize=16)
def _build_v7_prompt(stage: str) -> str:
    if stage not in STAGE_VERSIONS[PROMPT_FAMILY_VERSION]:
        raise PromptFamilyError(f"Unknown prompt stage: {stage!r}")
    shared = _read_prompt_file(_V7_FILES["shared"])
    stage_text = _read_prompt_file(_V7_FILES[stage])
    return (
        f"{shared}\n\n"
        "<!-- PROMPT_STAGE_BOUNDARY: shared-policy-v7 -> "
        f"{_V7_FILES[stage]} -->\n\n{stage_text}\n"
    )


def build_stage_prompt(stage: str, family: Optional[str] = None) -> str:
    resolved = resolve_prompt_family(family)
    if resolved == PROMPT_FAMILY_VERSION:
        return _build_v7_prompt(stage)
    filename = _LEGACY_STAGE_FILES.get(stage)
    if filename:
        return _read_prompt_file(filename)
    legacy_stage = {
        "research": "research",
        "assessment": "decision",
        "reason": "watch_reason",
    }.get(stage)
    if legacy_stage:
        # Local import avoids the prompt_family <-> llm_caller module cycle while
        # hashing the exact fragment used by the supported legacy split caller.
        from app.signals import llm_caller

        return llm_caller._load_system_prompt(
            stage=legacy_stage,
            version=llm_caller.PROMPT_VERSION_PARITY,
        )
    raise PromptFamilyError(f"Unknown prompt stage: {stage!r}")


def assembled_prompt_sha256(stage: str, family: Optional[str] = None) -> str:
    return hashlib.sha256(build_stage_prompt(stage, family).encode("utf-8")).hexdigest()


def prompt_metadata(family: Optional[str] = None) -> Dict[str, Any]:
    resolved = resolve_prompt_family(family)
    versions = STAGE_VERSIONS[resolved]
    sha: Dict[str, str] = {}
    for stage in versions:
        sha[stage] = assembled_prompt_sha256(stage, resolved)
    return {
        "prompt_family_version": resolved,
        "shared_policy_version": (
            SHARED_POLICY_VERSION if resolved == PROMPT_FAMILY_VERSION else None
        ),
        "research_prompt_version": versions["research"],
        "assessment_prompt_version": versions["assessment"],
        "global_selector_version": versions["global_selector"],
        "reason_prompt_version": versions["reason"],
        "tracking_prompt_version": versions["tracking"],
        "tracking_state_machine_version": TRACKING_STATE_MACHINE_VERSION,
        "response_contract_versions": (
            RESPONSE_CONTRACT_VERSIONS
            if resolved == PROMPT_FAMILY_VERSION
            else {}
        ),
        "prompt_sha256": sha,
    }


def payload_metrics(
    *,
    system_prompt: str,
    user_payload: Any,
    candidate_count: int,
    estimated_output_reserve: int,
    model_context_limit: int,
) -> Dict[str, Any]:
    user_text = (
        user_payload
        if isinstance(user_payload, str)
        else json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))
    )
    serialized = f"{system_prompt}\n{user_text}".encode("utf-8")
    return {
        "candidate_count": candidate_count,
        "serialized_bytes": len(serialized),
        "estimated_input_tokens": max(1, (len(serialized) + 2) // 3),
        "estimated_output_reserve": estimated_output_reserve,
        "model_context_limit": model_context_limit,
    }


def research_input(
    rows: Iterable[Mapping[str, Any]],
    *,
    research_date: str,
    market_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    market = market_context or {}
    items = []
    for row in rows:
        theme = row.get("theme_candidates")
        if not isinstance(theme, list):
            theme = [
                value for value in (row.get("theme_cluster"), row.get("industry"))
                if value
            ]
        items.append({
            "date": research_date,
            "stock": str(row.get("stock") or row.get("stock_id") or ""),
            "name": row.get("name"),
            "asset_type": row.get("asset_type") or "COMMON_STOCK",
            "industry": row.get("industry"),
            "sub_industry": row.get("sub_industry"),
            "phase2_role": row.get("role") or row.get("phase2_role"),
            "theme_candidates": theme,
            "backend_research_context": {
                "market_regime": market.get("market_regime"),
                "candidate_sources": row.get("candidate_sources") or row.get("sources") or [],
                "theme_cluster": row.get("theme_cluster"),
                "group_name": row.get("group_name"),
            },
        })
    return {"date": research_date, "items": items}


def assessment_input(
    rows: Iterable[Mapping[str, Any]], *, assessment_date: str
) -> Dict[str, Any]:
    items = []
    for row in rows:
        items.append({
            "date": assessment_date,
            "stock": str(row.get("stock") or row.get("stock_id") or ""),
            "asset_type": row.get("asset_type"),
            "backend_max_decision": row.get("backend_max_decision"),
            "momentum_freshness": row.get("momentum_freshness"),
            "quality_evidence": row.get("quality_evidence") or {},
            "research": {
                key: row.get(key)
                for key in (
                    "instrument_validation", "business_validation",
                    "theme_validation", "supply_chain_validation",
                    "material_contradictions", "research_confidence",
                    "research_summary",
                )
            },
        })
    return {"date": assessment_date, "items": items}


def reason_input(
    rows: Iterable[Mapping[str, Any]], *, reason_date: str
) -> Dict[str, Any]:
    items = []
    for row in rows:
        margin_data = row.get("margin_analysis") or row.get("margin_data")
        if not isinstance(margin_data, dict):
            margin_data = {
                "close_price": row.get("close_1d") or row.get("close_price"),
                "margin_balance_shares": row.get("margin_balance_shares"),
                "margin_change_shares": row.get("margin_change_shares"),
                "short_balance_shares": row.get("short_balance_shares"),
                "short_change_shares": row.get("short_change_shares"),
                "margin_short_ratio_pct": row.get("margin_short_ratio_pct"),
            }
        momentum_summary = row.get("momentum_signals")
        if not isinstance(momentum_summary, dict):
            momentum_summary = {
                key: row.get(key)
                for key in (
                    "momentum_score", "momentum_grade", "momentum_phase",
                    "return_20d", "return_60d",
                    "rs_market_percentile_20d",
                    "rs_industry_percentile_20d",
                    "rs_rank_change_5d", "trend_efficiency_20d",
                    "distance_to_high_20d_pct", "atr_pct_14d",
                    "momentum_freshness",
                )
            }
        items.append({
            "date": reason_date,
            "stock": str(row.get("stock") or row.get("stock_id") or ""),
            "asset_type": row.get("asset_type"),
            "recommendation_thesis": row.get("recommendation_thesis"),
            "relative_advantage": row.get("relative_advantage"),
            "research": {
                key: row.get(key)
                for key in (
                    "instrument_summary", "business_summary", "theme",
                    "supply_chain_role", "research_summary", "sources",
                )
            },
            "backend_summary": {
                key: row.get(key)
                for key in (
                    "role", "tracking_state", "entry_state", "technical_status",
                    "backend_priority_rank", "market_regime",
                )
            },
            "evidence": {
                "institutional": row.get("institutional_summary"),
                "signals": row.get("signals") or row.get("deterministic_signals") or {},
                "quality": row.get("quality_evidence") or {},
            },
            "margin_data": margin_data,
            "momentum_summary": momentum_summary,
        })
    return {"date": reason_date, "items": items}


def research_output_schema(
    *, expected_stocks: Iterable[str], expected_date: str
) -> Dict[str, Any]:
    """Strict Responses API schema for the v7 research stage.

    Backend validators remain authoritative for one-to-one alignment, date
    boundaries and Traditional Chinese requirements.  This schema prevents the
    common transport-level failures first: truncated prose, markdown fences,
    missing containers and free-form enum values.
    """
    stock_ids = [str(stock) for stock in expected_stocks]
    nullable_string = {"type": ["string", "null"]}
    contradiction = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "type": {
                "type": "string",
                "enum": [
                    "BUSINESS_MISMATCH",
                    "THEME_MISMATCH",
                    "FALSE_SUPPLY_CHAIN_LINK",
                    "MATERIAL_NEGATIVE_EVENT",
                    "DATA_CONTRADICTION",
                ],
            },
            "summary": {"type": "string"},
            "url": {"type": "string"},
            "published_date": {"type": "string"},
        },
        "required": ["type", "summary", "url", "published_date"],
    }
    source = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "published_date": {"type": "string"},
            "source_type": {
                "type": "string",
                "enum": [
                    "COMPANY",
                    "EXCHANGE",
                    "ETF_ISSUER",
                    "NEWS",
                    "GOVERNMENT",
                    "OTHER",
                ],
            },
        },
        "required": ["title", "url", "published_date", "source_type"],
    }
    item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stock": {"type": "string", "enum": stock_ids},
            "instrument_validation": {
                "type": "string",
                "enum": ["VERIFIED", "UNCONFIRMED", "MISMATCH"],
            },
            "theme_validation": {
                "type": "string",
                "enum": ["VERIFIED", "UNCONFIRMED", "MISMATCH"],
            },
            "supply_chain_validation": {
                "type": "string",
                "enum": [
                    "VERIFIED",
                    "UNCONFIRMED",
                    "MISMATCH",
                    "NOT_APPLICABLE",
                ],
            },
            "instrument_summary": {"type": "string"},
            "theme": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "duration": {
                        "type": "string",
                        "enum": ["short", "1Q", "2Q_plus", "unclear"],
                    },
                    "maturity": {
                        "type": "string",
                        "enum": ["early", "mid", "late", "post_event", "unclear"],
                    },
                    "catalyst_status": {
                        "type": "string",
                        "enum": ["ACTIVE", "WEAKENING", "EXPIRED", "UNCONFIRMED"],
                    },
                    "catalyst_summary": {"type": "string"},
                },
                "required": [
                    "name",
                    "duration",
                    "maturity",
                    "catalyst_status",
                    "catalyst_summary",
                ],
            },
            "supply_chain_role": {"type": "string"},
            "group_name": nullable_string,
            "theme_cluster": nullable_string,
            "material_contradictions": {
                "type": "array",
                "items": contradiction,
            },
            "sources": {"type": "array", "items": source},
            "research_confidence": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "LOW"],
            },
            "research_summary": {"type": "string"},
        },
        "required": [
            "stock",
            "instrument_validation",
            "theme_validation",
            "supply_chain_validation",
            "instrument_summary",
            "theme",
            "supply_chain_role",
            "group_name",
            "theme_cluster",
            "material_contradictions",
            "sources",
            "research_confidence",
            "research_summary",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "date": {"type": "string", "enum": [expected_date]},
            "items": {"type": "array", "items": item},
        },
        "required": ["date", "items"],
    }


def assessment_output_schema(
    *, expected_stocks: Iterable[str], expected_date: str
) -> Dict[str, Any]:
    stock_ids = [str(stock) for stock in expected_stocks]
    veto_reasons = [
        "BACKEND_MAX_REMOVE",
        "BUSINESS_MISMATCH",
        "THEME_MISMATCH",
        "FALSE_SUPPLY_CHAIN_LINK",
        "MATERIAL_NEGATIVE_EVENT",
        "DATA_CONTRADICTION",
        "INSUFFICIENT_CONFIRMATION",
        "MOMENTUM_NOT_FRESH",
        "WEAK_PARTICIPATION",
        "CATALYST_TOO_WEAK",
        "EVIDENCE_NOT_COHERENT",
    ]
    item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stock": {"type": "string", "enum": stock_ids},
            "assessment": {
                "type": "string",
                "enum": ["ELIGIBLE_FOR_GLOBAL_SELECTION", "REMOVE"],
            },
            "veto_reason": {
                "type": ["string", "null"],
                "enum": [None, *veto_reasons],
            },
            "assessment_reason": {"type": "string"},
            "quality_assessment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "momentum_quality": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                    "participation_quality": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                    "catalyst_quality": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW", "UNCONFIRMED"],
                    },
                    "evidence_coherence": {
                        "type": "string",
                        "enum": ["STRONG", "MODERATE", "WEAK"],
                    },
                },
                "required": [
                    "momentum_quality",
                    "participation_quality",
                    "catalyst_quality",
                    "evidence_coherence",
                ],
            },
            "veto_evidence": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "summary": {"type": ["string", "null"]},
                    "urls": {"type": "array", "items": {"type": "string"}},
                    "published_dates": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["summary", "urls", "published_dates"],
            },
        },
        "required": [
            "stock",
            "assessment",
            "veto_reason",
            "assessment_reason",
            "quality_assessment",
            "veto_evidence",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "date": {"type": "string", "enum": [expected_date]},
            "items": {"type": "array", "items": item},
        },
        "required": ["date", "items"],
    }


def reason_output_schema(
    *, expected_stocks: Iterable[str], expected_date: str
) -> Dict[str, Any]:
    stock_ids = [str(stock) for stock in expected_stocks]
    bullets = {"type": "array", "items": {"type": "string"}}
    nullable_number = {"type": ["number", "null"]}
    item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stock": {"type": "string", "enum": stock_ids},
            "theme_reason": bullets,
            "capital_reason": bullets,
            "chip_reason": bullets,
            "margin_reason": bullets,
            "technical_reason": bullets,
            "momentum_reason": bullets,
            "margin_analysis": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stock_table": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "close_price": nullable_number,
                            "margin_balance_shares": nullable_number,
                            "margin_change_shares": nullable_number,
                            "short_balance_shares": nullable_number,
                            "short_change_shares": nullable_number,
                            "margin_short_ratio_pct": nullable_number,
                        },
                        "required": [
                            "close_price",
                            "margin_balance_shares",
                            "margin_change_shares",
                            "short_balance_shares",
                            "short_change_shares",
                            "margin_short_ratio_pct",
                        ],
                    },
                    "stock_interpretation": {"type": "string"},
                    "stock_conclusion": {"type": "string"},
                    "market_summary": {"type": "string"},
                    "risk_note": {"type": "string"},
                    "weight_ratio": {"type": "string"},
                },
                "required": [
                    "stock_table",
                    "stock_interpretation",
                    "stock_conclusion",
                    "market_summary",
                    "risk_note",
                    "weight_ratio",
                ],
            },
        },
        "required": [
            "stock",
            "theme_reason",
            "capital_reason",
            "chip_reason",
            "margin_reason",
            "technical_reason",
            "momentum_reason",
            "margin_analysis",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "date": {"type": "string", "enum": [expected_date]},
            "items": {"type": "array", "items": item},
        },
        "required": ["date", "items"],
    }


def tracking_output_schema(
    *, expected_stocks: Iterable[str], review_date: str
) -> Dict[str, Any]:
    stock_ids = [str(stock) for stock in expected_stocks]
    validation = ["VERIFIED", "UNCONFIRMED", "MISMATCH"]
    dimension = ["INTACT", "WEAKENING", "INVALIDATED", "UNKNOWN"]
    invalidation_reasons = [
        "BUSINESS_MISMATCH",
        "THEME_MISMATCH",
        "FALSE_SUPPLY_CHAIN_LINK",
        "MATERIAL_NEGATIVE_EVENT",
        "DATA_CONTRADICTION",
    ]
    item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stock": {"type": "string", "enum": stock_ids},
            "assessment": {
                "type": "string",
                "enum": [
                    "THESIS_INTACT",
                    "THESIS_WEAKENING",
                    "THESIS_INVALIDATED",
                    "RESEARCH_UNAVAILABLE",
                ],
            },
            "instrument_validation": {"type": "string", "enum": validation},
            "theme_validation": {"type": "string", "enum": validation},
            "supply_chain_validation": {
                "type": "string",
                "enum": [*validation, "NOT_APPLICABLE"],
            },
            "catalyst_status": {
                "type": "string",
                "enum": [
                    "ACTIVE",
                    "WEAKENING",
                    "EXPIRED",
                    "REPLACED",
                    "UNCONFIRMED",
                ],
            },
            "thesis_dimensions": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "business_or_exposure": {"type": "string", "enum": dimension},
                    "theme": {"type": "string", "enum": dimension},
                    "catalyst": {"type": "string", "enum": dimension},
                },
                "required": ["business_or_exposure", "theme", "catalyst"],
            },
            "invalidation_reason_code": {
                "type": ["string", "null"],
                "enum": [None, *invalidation_reasons],
            },
            "assessment_reason": {"type": "string"},
            "material_evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "summary": {"type": "string"},
                        "url": {"type": "string"},
                        "published_date": {"type": "string"},
                    },
                    "required": ["summary", "url", "published_date"],
                },
            },
        },
        "required": [
            "stock",
            "assessment",
            "instrument_validation",
            "theme_validation",
            "supply_chain_validation",
            "catalyst_status",
            "thesis_dimensions",
            "invalidation_reason_code",
            "assessment_reason",
            "material_evidence",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "review_date": {"type": "string", "enum": [review_date]},
            "items": {"type": "array", "items": item},
        },
        "required": ["review_date", "items"],
    }


def validate_research_output(
    payload: Any, *, expected_stocks: Iterable[str], expected_date: str
) -> List[Dict[str, Any]]:
    rows = _validated_items(payload, expected_stocks, "date", expected_date)
    for item in rows:
        _enum(item, "instrument_validation", {"VERIFIED", "UNCONFIRMED", "MISMATCH"})
        _enum(item, "theme_validation", {"VERIFIED", "UNCONFIRMED", "MISMATCH"})
        _enum(item, "supply_chain_validation", {
            "VERIFIED", "UNCONFIRMED", "MISMATCH", "NOT_APPLICABLE"
        })
        _enum(item, "research_confidence", {"HIGH", "MEDIUM", "LOW"})
        _traditional_text(item, "instrument_summary")
        _traditional_text(item, "research_summary")
        theme = _mapping(item, "theme")
        _enum(theme, "duration", {"short", "1Q", "2Q_plus", "unclear"})
        _enum(theme, "maturity", {"early", "mid", "late", "post_event", "unclear"})
        _enum(theme, "catalyst_status", {"ACTIVE", "WEAKENING", "EXPIRED", "UNCONFIRMED"})
        _traditional_text(theme, "catalyst_summary")
        item["sources"] = _evidence_rows(
            item.get("sources"), expected_date, require_summary=False
        )
        for source in item["sources"]:
            _human_text(source, "title")
            _enum(source, "source_type", {
                "COMPANY", "EXCHANGE", "ETF_ISSUER", "NEWS",
                "GOVERNMENT", "OTHER",
            })
        contradictions = item.get("material_contradictions")
        if not isinstance(contradictions, list):
            raise PromptFamilyError("Research material_contradictions must be a list.")
        kept_contradictions: List[Dict[str, Any]] = []
        for evidence in contradictions:
            if not isinstance(evidence, dict):
                raise PromptFamilyError("Research contradiction must be an object.")
            _enum(evidence, "type", {
                "BUSINESS_MISMATCH", "THEME_MISMATCH", "FALSE_SUPPLY_CHAIN_LINK",
                "MATERIAL_NEGATIVE_EVENT", "DATA_CONTRADICTION",
            })
            _traditional_text(evidence, "summary")
            _valid_url(evidence.get("url"))
            if not _valid_published_date(evidence.get("published_date"), expected_date):
                continue
            kept_contradictions.append(evidence)
        item["material_contradictions"] = kept_contradictions
    return rows


def validate_assessment_output(
    payload: Any, *, expected_stocks: Iterable[str], expected_date: str
) -> List[Dict[str, Any]]:
    rows = _validated_items(payload, expected_stocks, "date", expected_date)
    for item in rows:
        assessment = _enum(
            item, "assessment", {"ELIGIBLE_FOR_GLOBAL_SELECTION", "REMOVE"}
        )
        veto_reason = item.get("veto_reason")
        allowed_veto = {
            "BACKEND_MAX_REMOVE", "BUSINESS_MISMATCH", "THEME_MISMATCH",
            "FALSE_SUPPLY_CHAIN_LINK", "MATERIAL_NEGATIVE_EVENT",
            "DATA_CONTRADICTION", "INSUFFICIENT_CONFIRMATION",
            "MOMENTUM_NOT_FRESH", "WEAK_PARTICIPATION",
            "CATALYST_TOO_WEAK", "EVIDENCE_NOT_COHERENT",
        }
        if veto_reason is not None and veto_reason not in allowed_veto:
            raise PromptFamilyError(f"Invalid veto_reason: {veto_reason!r}.")
        _traditional_text(item, "assessment_reason")
        quality = _mapping(item, "quality_assessment")
        for key in ("momentum_quality", "participation_quality"):
            _enum(quality, key, {"HIGH", "MEDIUM", "LOW"})
        _enum(quality, "catalyst_quality", {"HIGH", "MEDIUM", "LOW", "UNCONFIRMED"})
        _enum(quality, "evidence_coherence", {"STRONG", "MODERATE", "WEAK"})
        veto = _mapping(item, "veto_evidence")
        urls = veto.get("urls")
        published = veto.get("published_dates")
        if not isinstance(urls, list) or not isinstance(published, list):
            raise PromptFamilyError("Assessment veto evidence lists are required.")
        for value in urls:
            _valid_url(value)
        veto["published_dates"] = [
            value for value in published if _valid_published_date(value, expected_date)
        ]
        if assessment == "REMOVE" and not item.get("veto_reason"):
            raise PromptFamilyError("REMOVE requires veto_reason.")
    return rows


def validate_reason_output(
    payload: Any, *, expected_stocks: Iterable[str], expected_date: str
) -> List[Dict[str, Any]]:
    rows = _validated_items(payload, expected_stocks, "date", expected_date)
    for item in rows:
        for key in (
            "theme_reason", "capital_reason", "chip_reason",
            "technical_reason", "momentum_reason",
        ):
            _bullet_list(item, key, minimum=2, maximum=4)
        _bullet_list(item, "margin_reason", minimum=1, maximum=3)
        if not isinstance(item.get("margin_analysis"), dict):
            raise PromptFamilyError("Reason margin_analysis must be an object.")
    return rows


def _validated_items(
    payload: Any,
    expected_stocks: Iterable[str],
    date_key: str,
    expected_date: str,
) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get(date_key) != expected_date:
        raise PromptFamilyError(f"Prompt output {date_key} mismatch.")
    items = payload.get("items")
    if not isinstance(items, list):
        raise PromptFamilyError("Prompt output items must be a list.")
    expected = [str(stock) for stock in expected_stocks]
    actual = [str(item.get("stock")) for item in items if isinstance(item, dict)]
    if len(actual) != len(items) or len(actual) != len(set(actual)):
        raise PromptFamilyError("Prompt output contains invalid or duplicate stocks.")
    if set(actual) != set(expected) or len(actual) != len(expected):
        raise PromptFamilyError("Prompt output stock alignment is incomplete.")
    by_stock = {str(item["stock"]): item for item in items}
    return [by_stock[stock] for stock in expected]


def _mapping(item: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = item.get(key)
    if not isinstance(value, dict):
        raise PromptFamilyError(f"{key} must be an object.")
    return value


def _enum(item: Mapping[str, Any], key: str, allowed: set[str]) -> str:
    value = item.get(key)
    if value not in allowed:
        raise PromptFamilyError(f"Invalid {key}: {value!r}.")
    return str(value)


def _human_text(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PromptFamilyError(f"{key} must be non-empty Traditional Chinese text.")
    return value.strip()


def is_traditional_chinese_text(value: Any) -> bool:
    """Conservative machine check: human text must contain a CJK character.

    Full Traditional-vs-Simplified language detection is not deterministic enough
    for a hard gate. Prompt policy and focused tests cover terminology; this check
    reliably rejects accidental English-only stage prose.
    """
    return isinstance(value, str) and any(
        "\u3400" <= char <= "\u9fff" for char in value
    )


def _traditional_text(item: Mapping[str, Any], key: str) -> str:
    value = _human_text(item, key)
    if not is_traditional_chinese_text(value):
        raise PromptFamilyError(f"{key} must contain Traditional Chinese text.")
    return value


def _bullet_list(
    item: Mapping[str, Any], key: str, *, minimum: int, maximum: int
) -> None:
    value = item.get(key)
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= maximum
        or any(not isinstance(part, str) or not part.strip() for part in value)
    ):
        raise PromptFamilyError(f"{key} must contain {minimum}..{maximum} bullets.")
    if any(not is_traditional_chinese_text(part) for part in value):
        raise PromptFamilyError(f"{key} bullets must contain Traditional Chinese text.")


def _valid_url(value: Any) -> None:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PromptFamilyError(f"Invalid evidence URL: {value!r}.")


def _valid_published_date(value: Any, cutoff: str) -> bool:
    """Return True when ``value`` is a real ISO date at/before ``cutoff``.

    An unparseable value (for example the model writing "2026-07-??" when it
    only knows the month) is not a leak risk -- there is no confirmed date to
    check -- so callers drop that single evidence entry instead of failing
    the whole item.  A value that *does* parse and *is* after cutoff remains
    a hard contract violation, since that is an actual future-info leak.
    """
    try:
        published = date.fromisoformat(str(value))
    except ValueError:
        return False
    boundary = date.fromisoformat(cutoff)
    if published > boundary:
        raise PromptFamilyError("Evidence published date is after the stage cutoff.")
    return True


def _evidence_rows(
    value: Any, cutoff: str, *, require_summary: bool
) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise PromptFamilyError("Evidence sources must be a list.")
    kept: List[Dict[str, Any]] = []
    for evidence in value:
        if not isinstance(evidence, dict):
            raise PromptFamilyError("Evidence source must be an object.")
        if require_summary:
            _human_text(evidence, "summary")
        _valid_url(evidence.get("url"))
        if not _valid_published_date(evidence.get("published_date"), cutoff):
            continue
        kept.append(evidence)
    return kept
