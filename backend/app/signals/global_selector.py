"""P3 global recommendation selection.

Every successfully researched, non-vetoed Phase 2 candidate is represented by one
compact card and compared in one atomic LLM call.  This module intentionally has
no Top-K, selection ratio, source quota, asset quota, or cluster cap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from app.signals import llm_caller, prompt_family


SELECTION_VERSION = "v7_global_selector"
ASSESSMENT_VERSION = "v7_assessment"
REASON_VERSION = "v7_reason"
DEFAULT_GLOBAL_SELECTION_MODEL = os.getenv(
    "OPENAI_SIGNALS_GLOBAL_SELECTION_MODEL",
    llm_caller.DEFAULT_DECISION_MODEL,
).strip()

NOT_SELECTED_REASON_CODES = {
    "LOWER_RELATIVE_PRIORITY",
    "POSITIVE_CASE_INCOMPLETE",
    "CATALYST_UNCONFIRMED",
    "PARTICIPATION_NOT_DISTINCTIVE",
    "EVIDENCE_COHERENCE_WEAK",
    "THESIS_OVERLAP",
    "SETUP_NEEDS_CONFIRMATION",
    "RESEARCH_CONFIDENCE_LOW",
    "NO_DISTINCT_DAILY_EDGE",
}
FACTUAL_VETO_REASONS = {
    "BUSINESS_MISMATCH",
    "THEME_MISMATCH",
    "FALSE_SUPPLY_CHAIN_LINK",
    "MATERIAL_NEGATIVE_EVENT",
    "DATA_CONTRADICTION",
}
QUALITY_VETO_REASONS = {
    "INSUFFICIENT_CONFIRMATION",
    "MOMENTUM_NOT_FRESH",
    "WEAK_PARTICIPATION",
    "CATALYST_TOO_WEAK",
    "EVIDENCE_NOT_COHERENT",
}
RECOMMENDATION_BASIS_CODES = {
    "MOMENTUM",
    "PARTICIPATION",
    "CATALYST",
    "RELATIVE_ADVANTAGE",
}

_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "prompts"
    / "global-recommendation-selector-v1.md"
)
_CACHE_KEY = "signals:p3:global-selector:v1"
# 2026-08-05／2026-08-10 production incidents: candidate pools spiked to
# 135／116 eligible cards and the fixed 16,000 token reserve was too small —
# every item requires a non-nullable `selection_reason` plus ~13 other
# schema fields (nullable but still emitted as keys under Structured
# Outputs), so per-item token cost does not shrink just because most items
# end up NOT_SELECTED. All 3 contract retries hit `max_output_tokens`
# truncation and the day's watchlist came back empty. The reserve now scales
# with candidate count instead of a flat number; `within_limit` below still
# guards against genuinely oversized pools by raising a clear
# GLOBAL_SELECTION_CONTEXT_EXCEEDED instead of silently truncating output.
_OUTPUT_TOKEN_RESERVE_BASE = 3_000
_OUTPUT_TOKEN_RESERVE_PER_CANDIDATE = 220
_DEFAULT_CONTEXT_LIMIT_TOKENS = 114_688


def _default_output_token_reserve(candidate_count: int) -> int:
    return _OUTPUT_TOKEN_RESERVE_BASE + candidate_count * _OUTPUT_TOKEN_RESERVE_PER_CANDIDATE


def global_selection_output_schema(
    *, expected_version: str, selection_date: str, expected_stocks: Iterable[str]
) -> Dict[str, Any]:
    """Strict Responses API schema for the atomic global selector."""
    stock_ids = [str(stock) for stock in expected_stocks]
    nullable_string = {"type": ["string", "null"]}
    item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stock": {"type": "string", "enum": stock_ids},
            "decision": {
                "type": "string",
                "enum": ["RECOMMEND", "NOT_SELECTED"],
            },
            "recommendation_rank": {"type": ["integer", "null"]},
            "selection_reason_code": {
                "type": ["string", "null"],
                "enum": [None, *sorted(NOT_SELECTED_REASON_CODES)],
            },
            "selection_reason": {"type": "string"},
            "recommendation_thesis": nullable_string,
            "relative_advantage": nullable_string,
            "theme_cluster": nullable_string,
            "distinct_thesis": {"type": "boolean"},
            "overlap_with": {
                "type": "array",
                "items": {"type": "string", "enum": stock_ids},
            },
            "overlap_reason": nullable_string,
            "rank_override": {"type": "boolean"},
            "rank_override_reason": nullable_string,
            "recommendation_basis": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": sorted(RECOMMENDATION_BASIS_CODES),
                },
            },
        },
        "required": [
            "stock",
            "decision",
            "recommendation_rank",
            "selection_reason_code",
            "selection_reason",
            "recommendation_thesis",
            "relative_advantage",
            "theme_cluster",
            "distinct_thesis",
            "overlap_with",
            "overlap_reason",
            "rank_override",
            "rank_override_reason",
            "recommendation_basis",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "selection_version": {
                "type": "string",
                "enum": [expected_version],
            },
            "date": {"type": "string", "enum": [selection_date]},
            "selection_complete": {"type": "boolean", "enum": [True]},
            "items": {"type": "array", "items": item},
            # eligible/recommend/not_selected counts are intentionally not
            # part of this contract.  They are 100% derivable from `items`
            # and `cards`, which the backend already recomputes for the
            # returned summary regardless of what the model reports (see
            # `validate_global_selection`).  Requiring the model to also
            # echo a matching count across ~25+ cards added an arithmetic
            # failure mode with no correctness benefit: production replay
            # showed the model miscounting on both the initial attempt and
            # its one contract-retry, wasting the retry and forcing an
            # atomic GLOBAL_SELECTION_FAILED even though every item-level
            # decision was valid.
            "summary": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "selection_rationale": {"type": "string"},
                },
                "required": ["selection_rationale"],
            },
        },
        "required": [
            "selection_version",
            "date",
            "selection_complete",
            "items",
            "summary",
        ],
    }


@dataclass(frozen=True)
class SelectionCapacity:
    candidate_count: int
    serialized_bytes: int
    estimated_input_tokens: int
    output_token_reserve: int
    model_context_limit_tokens: int
    within_limit: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "serialized_bytes": self.serialized_bytes,
            "estimated_input_tokens": self.estimated_input_tokens,
            "output_token_reserve": self.output_token_reserve,
            "model_context_limit_tokens": self.model_context_limit_tokens,
            "within_limit": self.within_limit,
        }


class GlobalSelectionError(RuntimeError):
    """Atomic global-selection failure with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostic: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic or {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "stock": None,
            "stage": "GLOBAL_SELECTION",
            "status": "GLOBAL_SELECTION_FAILED",
            "processing_status": "GLOBAL_SELECTION_FAILED",
            "error_code": self.code,
            "error_summary": str(self)[:500],
            "diagnostic": self.diagnostic,
        }


def partition_assessments(
    assessments: Iterable[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Separate true REMOVE rows from globally selectable rows.

    An LLM-produced REMOVE is not trusted on its face.  Factual vetoes require the
    matching validation contradiction.  Quality vetoes require both Phase 2 quality
    evidence and a matching LOW/WEAK assessment.  Invalid or unsupported vetoes stay
    eligible for relative comparison and carry an audit note.
    """
    eligible: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for source in assessments:
        item = dict(source)
        requested = str(
            item.get("assessment_status") or item.get("decision") or "ELIGIBLE"
        ).upper()
        veto = str(item.get("veto_reason") or "").upper() or None
        backend_max = str(item.get("backend_max_decision") or "").upper()

        valid_remove = False
        if backend_max == "REMOVE":
            valid_remove = True
            veto = veto or "BACKEND_MAX_REMOVE"
        elif requested == "REMOVE" and veto in FACTUAL_VETO_REASONS:
            valid_remove = _factual_veto_has_prerequisite(item, veto)
        elif requested == "REMOVE" and veto in QUALITY_VETO_REASONS:
            valid_remove = _quality_veto_has_prerequisite(item, veto)

        if valid_remove:
            item["assessment_status"] = "REMOVE"
            item["decision"] = "REMOVE"
            item["veto_reason"] = veto
            item["selection_status"] = "REMOVE"
            removed.append(item)
            continue

        if requested == "REMOVE":
            item["assessment_guardrail_note"] = (
                f"Unsupported REMOVE veto {veto or 'MISSING'} was not applied."
            )
        item["assessment_status"] = "ELIGIBLE"
        item["decision"] = "WATCH"  # legacy compatibility; not a final recommendation.
        item["veto_reason"] = None
        item["selection_status"] = None
        eligible.append(item)
    return eligible, removed


def build_compact_selection_cards(
    eligible: Iterable[Dict[str, Any]],
    *,
    selection_date: Union[date, str],
) -> List[Dict[str, Any]]:
    """Project every eligible row into one bounded, same-shape comparison card."""
    rows = list(eligible)
    total = len(rows)
    date_text = selection_date.isoformat() if hasattr(selection_date, "isoformat") else str(selection_date)
    cards: List[Dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        momentum = item.get("momentum_signals")
        if not isinstance(momentum, dict):
            momentum = {
                key: item.get(key)
                for key in (
                    "momentum_score",
                    "momentum_grade",
                    "momentum_phase",
                    "return_5d",
                    "return_20d",
                    "return_60d",
                    "rs_market_percentile_20d",
                    "rs_industry_percentile_20d",
                    "trend_efficiency_20d",
                    "distance_to_high_20d_pct",
                    "atr_pct_14d",
                )
            }
        theme = item.get("theme") if isinstance(item.get("theme"), dict) else {}
        group = item.get("group_info") if isinstance(item.get("group_info"), dict) else {}
        quality = (
            item.get("quality_assessment")
            if isinstance(item.get("quality_assessment"), dict)
            else {}
        )
        validations = {
            "business": item.get("business_validation"),
            "theme": item.get("theme_validation"),
            "supply_chain": (
                "NOT_APPLICABLE"
                if str(item.get("asset_type") or "").upper() == "ETF"
                else item.get("supply_chain_validation")
            ),
        }
        signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
        deterministic = (
            item.get("deterministic_signals")
            if isinstance(item.get("deterministic_signals"), dict)
            else {}
        )
        quality_evidence = (
            item.get("quality_evidence")
            if isinstance(item.get("quality_evidence"), dict)
            else {}
        )
        candidate_sources = [
            source
            for source, present in (
                ("A", bool(item.get("from_a") or item.get("in_top_industries_3d"))),
                ("B", bool(item.get("from_b") or item.get("in_top_stocks_3d"))),
                ("C", bool(item.get("from_c"))),
                ("D", bool(item.get("from_d"))),
            )
            if present
        ]
        supply_chain_role = (
            "NOT_APPLICABLE"
            if str(item.get("asset_type") or "").upper() == "ETF"
            else item.get("supply_chain_position")
        )
        cards.append(
            {
                "as_of_date": date_text,
                "stock": str(item.get("stock") or item.get("stock_id") or ""),
                "name": item.get("name") or "",
                "asset_type": item.get("asset_type") or "COMMON_STOCK",
                "backend_priority_rank": index,
                "backend_priority_total": total,
                "backend_priority_percentile": (
                    round((total - index + 1) * 100 / total, 2) if total else None
                ),
                "phase2_role": item.get("role"),
                "phase2_tracking_state": item.get("tracking_state"),
                "phase2_entry_state": item.get("entry_state"),
                "regime_conviction": item.get("conviction")
                or item.get("regime_conviction"),
                "momentum_summary": {
                    "momentum_score": momentum.get("momentum_score"),
                    "momentum_grade": momentum.get("momentum_grade"),
                    "momentum_phase": momentum.get("momentum_phase"),
                    "market_rs": momentum.get("rs_market_percentile_20d"),
                    "peer_rs": momentum.get("rs_industry_percentile_20d"),
                    "rs_rank_change": momentum.get("rs_rank_change_5d")
                    or momentum.get("rs_rank_improvement_5d"),
                    "freshness": item.get("momentum_freshness"),
                    "price_structure": signals.get("technical_status")
                    or deterministic.get("technical_status"),
                },
                "participation_summary": {
                    "institution_flow_momentum": signals.get(
                        "institution_flow_momentum"
                    )
                    or deterministic.get("institution_flow_momentum"),
                    "institution_confirmation": bool(
                        quality_evidence.get("institution_confirmation")
                        or (item.get("total_institution_flow_3d") or 0) > 0
                    ),
                    "participation": bool(
                        quality_evidence.get("participation")
                        or quality_evidence.get("volume_confirmation")
                    ),
                    "sector_confirmation": bool(
                        quality_evidence.get("sector_confirmation")
                        or item.get("in_top_industries_3d")
                    ),
                    "chip_trend": signals.get("chip_trend")
                    or deterministic.get("chip_trend"),
                },
                "research_summary": {
                    "instrument_validation": validations["business"],
                    "theme_validation": validations["theme"],
                    "supply_chain_validation": validations["supply_chain"],
                    "theme_name": _trim(theme.get("main_theme"), 100),
                    "theme_duration": theme.get("theme_duration"),
                    "theme_maturity": theme.get("theme_maturity"),
                    "catalyst_summary": _trim(
                        item.get("catalyst_summary")
                        or theme.get("theme_reason"),
                        220,
                    ),
                    "instrument_summary": _trim(
                        item.get("instrument_summary")
                        or item.get("business_summary"),
                        220,
                    ),
                },
                "quality_assessment": quality,
                "risk_warnings": list(item.get("risk_warnings") or [])[:8],
                "candidate_sources": candidate_sources,
                "theme_cluster": item.get("theme_cluster")
                or theme.get("main_theme"),
                "supply_chain_role": supply_chain_role,
                "group_name": group.get("group_name"),
                "recommendation_thesis_candidate": _trim(
                    item.get("thesis_candidate")
                    or item.get("short_reason")
                    or theme.get("theme_reason"),
                    240,
                ),
                "research_confidence": item.get("research_confidence")
                or _derive_research_confidence(validations),
            }
        )
    return cards


def estimate_selection_capacity(cards: List[Dict[str, Any]]) -> SelectionCapacity:
    serialized = _serialize_cards(cards)
    byte_count = len(serialized.encode("utf-8"))
    # UTF-8 Chinese payloads are often token-dense. bytes/3 plus a small fixed
    # envelope is deliberately conservative and deterministic.
    estimated_tokens = math.ceil(byte_count / 3) + 2_000
    limit = _positive_env_int(
        "SIGNALS_GLOBAL_SELECTOR_CONTEXT_LIMIT_TOKENS",
        _DEFAULT_CONTEXT_LIMIT_TOKENS,
    )
    reserve = _positive_env_int(
        "SIGNALS_GLOBAL_SELECTOR_OUTPUT_TOKEN_RESERVE",
        _default_output_token_reserve(len(cards)),
    )
    return SelectionCapacity(
        candidate_count=len(cards),
        serialized_bytes=byte_count,
        estimated_input_tokens=estimated_tokens,
        output_token_reserve=reserve,
        model_context_limit_tokens=limit,
        within_limit=estimated_tokens + reserve <= limit,
    )


def run_global_selection(
    cards: List[Dict[str, Any]],
    market_context: Dict[str, Any],
    *,
    selection_date: Union[date, str],
    model: str = DEFAULT_GLOBAL_SELECTION_MODEL,
) -> Dict[str, Any]:
    """Run and validate the one-shot selector.  There is intentionally no fallback.

    A single full-set contract-correction retry is allowed for a semantically
    invalid model response.  The invalid response is never adopted, and the
    retry compares the complete card set again, so atomicity and the ban on
    tournament/partial selection remain intact.
    """
    family = prompt_family.resolve_prompt_family()
    expected_version = prompt_family.stage_version("global_selector", family)
    date_text = selection_date.isoformat() if hasattr(selection_date, "isoformat") else str(selection_date)
    capacity = estimate_selection_capacity(cards)
    if not capacity.within_limit:
        raise GlobalSelectionError(
            "GLOBAL_SELECTION_CONTEXT_EXCEEDED",
            "Compact selection cards exceed the configured one-shot model context.",
            diagnostic=capacity.as_dict(),
        )
    if not cards:
        return {
            "selection_version": expected_version,
            "date": date_text,
            "selection_complete": True,
            "items": [],
            "summary": {
                "eligible_count": 0,
                "recommend_count": 0,
                "not_selected_count": 0,
                "selection_rationale": "目前沒有可進入全體比較的候選股票。",
            },
            "capacity": capacity.as_dict(),
            "llm_diagnostic": {"status": "not_called_empty_input"},
        }
    try:
        system_prompt = prompt_family.build_stage_prompt(
            "global_selector", family
        )
    except (OSError, prompt_family.PromptFamilyError) as exc:
        raise GlobalSelectionError(
            "GLOBAL_SELECTION_PROMPT_MISSING",
            f"Global selector prompt could not be loaded: {exc}",
        ) from exc
    request_payload = {
        "selection_version": expected_version,
        "date": date_text,
        "compact_selection_cards": cards,
    }
    metadata = prompt_family.prompt_metadata(family)
    response_schema = (
        global_selection_output_schema(
            expected_version=expected_version,
            selection_date=date_text,
            expected_stocks=[str(card.get("stock") or "") for card in cards],
        )
        if family == prompt_family.PROMPT_FAMILY_VERSION
        else None
    )
    retry_enabled = (
        family == prompt_family.PROMPT_FAMILY_VERSION
        and os.getenv(
            "SIGNALS_GLOBAL_SELECTION_CONTRACT_RETRY", "true"
        ).strip().lower() not in {"0", "false", "no", "off"}
    )
    previous_error: Optional[GlobalSelectionError] = None
    max_attempts = 3 if retry_enabled else 1
    for attempt in range(max_attempts):
        call_payload = dict(request_payload)
        if previous_error is not None:
            call_payload["contract_retry"] = {
                "previous_error_code": previous_error.code,
                "previous_rejection": str(previous_error)[:1000],
                "required_correction": (
                    "重新比較完整 compact_selection_cards 並輸出完整一對一結果；"
                    "不可沿用部分結果。若任何較高 backend_priority_rank 候選為 "
                    "NOT_SELECTED，而較低順位候選為 RECOMMEND，該較低順位候選必須設 "
                    "rank_override=true，並以繁體中文提供非空 "
                    "rank_override_reason 與 relative_advantage。"
                ),
            }
        user_msg = json.dumps(
            call_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        payload, diagnostic = llm_caller._call_llm_json(
            system_prompt,
            user_msg,
            model=model,
            stage="global_selection",
            use_web_search=False,
            prompt_cache_key=f"signals:{family}:global-selector",
            max_output_tokens=capacity.output_token_reserve,
            candidate_count=len(cards),
            prompt_metadata={
                **metadata,
                "stage_prompt_version": expected_version,
                "assembled_prompt_sha256": metadata["prompt_sha256"][
                    "global_selector"
                ],
                "contract_retry_attempt": attempt,
            },
            response_schema=response_schema,
            response_format_name=(
                "fishtail_v7_global_selector"
                if family == prompt_family.PROMPT_FAMILY_VERSION
                else None
            ),
        )
        if payload is None:
            llm_failed_error = GlobalSelectionError(
                "GLOBAL_SELECTION_LLM_FAILED",
                "The global selector did not return valid JSON.",
                diagnostic=diagnostic,
            )
            if attempt < max_attempts - 1 and retry_enabled:
                previous_error = llm_failed_error
                continue
            raise llm_failed_error
        payload, normalized_recommendation_ranks = (
            _normalize_recommendation_ranks(payload, cards)
        )
        payload, derived_rank_overrides = _derive_rank_override_annotations(
            payload,
            cards,
        )
        try:
            validated = validate_global_selection(
                payload,
                cards,
                selection_date=date_text,
                expected_version=expected_version,
            )
        except GlobalSelectionError as exc:
            if attempt < max_attempts - 1 and retry_enabled:
                previous_error = exc
                continue
            if diagnostic and not exc.diagnostic:
                exc.diagnostic = diagnostic
            raise
        validated["capacity"] = capacity.as_dict()
        validated["llm_diagnostic"] = {
            **(diagnostic or {}),
            "contract_retry_attempt": attempt,
            "previous_contract_error": (
                previous_error.code if previous_error is not None else None
            ),
            "backend_derived_rank_overrides": derived_rank_overrides,
            "backend_normalized_recommendation_ranks": (
                normalized_recommendation_ranks
            ),
        }
        return validated
    raise AssertionError("unreachable global-selection retry state")


def _normalize_recommendation_ranks(
    payload: Any,
    cards: List[Dict[str, Any]],
) -> tuple[Any, List[Dict[str, Any]]]:
    """Normalize the selected set's display rank without changing membership.

    Valid model ranks retain their ordering.  Duplicate, missing, or invalid
    ranks are resolved by the existing backend priority backbone and original
    item order, then rewritten as the required continuous 1..N sequence.
    """

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload, []
    rank_by_stock = {
        str(card.get("stock") or ""): card.get("backend_priority_rank")
        for card in cards
    }
    normalized_items = [
        dict(item) if isinstance(item, dict) else item
        for item in payload["items"]
    ]
    recommended: List[tuple[int, Dict[str, Any]]] = [
        (index, item)
        for index, item in enumerate(normalized_items)
        if isinstance(item, dict)
        and str(item.get("decision") or "").upper() == "RECOMMEND"
    ]

    def order_key(entry: tuple[int, Dict[str, Any]]) -> tuple[int, int, int]:
        index, item = entry
        model_rank = item.get("recommendation_rank")
        valid_model_rank = (
            model_rank
            if isinstance(model_rank, int) and model_rank >= 1
            else 10**9
        )
        backend_rank = rank_by_stock.get(str(item.get("stock") or ""))
        valid_backend_rank = (
            backend_rank if isinstance(backend_rank, int) else 10**9
        )
        return valid_model_rank, valid_backend_rank, index

    changes: List[Dict[str, Any]] = []
    for new_rank, (_, item) in enumerate(
        sorted(recommended, key=order_key),
        start=1,
    ):
        old_rank = item.get("recommendation_rank")
        if old_rank != new_rank:
            changes.append(
                {
                    "stock": str(item.get("stock") or ""),
                    "from": old_rank,
                    "to": new_rank,
                }
            )
            item["recommendation_rank"] = new_rank
    return {**payload, "items": normalized_items}, changes


def _derive_rank_override_annotations(
    payload: Any,
    cards: List[Dict[str, Any]],
) -> tuple[Any, List[str]]:
    """Derive redundant rank-override annotations without changing decisions.

    The selector remains the sole source of RECOMMEND/NOT_SELECTED and of each
    candidate's relative advantage.  Backend rank crossing is an objective
    property of that complete decision set, so the backend derives the boolean
    and, when omitted, reuses the model-authored relative advantage as the
    explanation.  No decision, rank, thesis, or relative advantage is invented.
    """

    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload, []
    rank_by_stock = {
        str(card.get("stock") or ""): card.get("backend_priority_rank")
        for card in cards
    }
    excluded_ranks = [
        rank_by_stock.get(str(item.get("stock") or ""))
        for item in payload["items"]
        if isinstance(item, dict)
        and str(item.get("decision") or "").upper() == "NOT_SELECTED"
        and isinstance(
            rank_by_stock.get(str(item.get("stock") or "")),
            int,
        )
    ]
    if not excluded_ranks:
        return payload, []
    highest_excluded_rank = min(excluded_ranks)
    normalized_items: List[Any] = []
    derived: List[str] = []
    for raw in payload["items"]:
        if not isinstance(raw, dict):
            normalized_items.append(raw)
            continue
        item = dict(raw)
        stock_id = str(item.get("stock") or "")
        backend_rank = rank_by_stock.get(stock_id)
        if (
            str(item.get("decision") or "").upper() == "RECOMMEND"
            and isinstance(backend_rank, int)
            and backend_rank > highest_excluded_rank
        ):
            item["rank_override"] = True
            if not _nonempty(item.get("rank_override_reason")):
                relative_advantage = item.get("relative_advantage")
                if _nonempty(relative_advantage):
                    item["rank_override_reason"] = (
                        "跨越較高後端順位的依據為：" + str(relative_advantage)
                    )
            derived.append(stock_id)
        normalized_items.append(item)
    return {**payload, "items": normalized_items}, derived


def validate_global_selection(
    payload: Dict[str, Any],
    cards: List[Dict[str, Any]],
    *,
    selection_date: Union[date, str],
    expected_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate complete one-to-one alignment and every decision invariant in O(n)."""
    date_text = selection_date.isoformat() if hasattr(selection_date, "isoformat") else str(selection_date)
    version = expected_version or prompt_family.stage_version("global_selector")
    if payload.get("selection_version") != version:
        _invalid("GLOBAL_SELECTION_VERSION_MISMATCH", "selection_version mismatch")
    if str(payload.get("date") or "") != date_text:
        _invalid("GLOBAL_SELECTION_DATE_MISMATCH", "date mismatch")
    if payload.get("selection_complete") is not True:
        _invalid("GLOBAL_SELECTION_INCOMPLETE", "selection_complete must be true")
    items = payload.get("items")
    if not isinstance(items, list):
        _invalid("GLOBAL_SELECTION_SCHEMA_INVALID", "items must be a list")

    card_by_id = {str(card.get("stock") or ""): card for card in cards}
    expected_ids = set(card_by_id)
    seen: set[str] = set()
    normalized: List[Dict[str, Any]] = []
    recommend_ranks: List[int] = []
    not_selected_backend_ranks: List[int] = []
    for raw in items:
        if not isinstance(raw, dict):
            _invalid("GLOBAL_SELECTION_SCHEMA_INVALID", "every item must be an object")
        sid = str(raw.get("stock") or "")
        if sid not in expected_ids:
            _invalid("GLOBAL_SELECTION_UNKNOWN_STOCK", f"unknown stock: {sid}")
        if sid in seen:
            _invalid("GLOBAL_SELECTION_DUPLICATE_STOCK", f"duplicate stock: {sid}")
        seen.add(sid)
        decision = str(raw.get("decision") or "").upper()
        if decision not in {"RECOMMEND", "NOT_SELECTED"}:
            _invalid("GLOBAL_SELECTION_DECISION_INVALID", f"invalid decision for {sid}")
        item = dict(raw)
        item["stock"] = sid
        item["decision"] = decision
        backend_rank = int(card_by_id[sid]["backend_priority_rank"])
        item["backend_priority_rank"] = backend_rank
        item["backend_priority_total"] = card_by_id[sid]["backend_priority_total"]
        item["backend_priority_percentile"] = card_by_id[sid][
            "backend_priority_percentile"
        ]
        # Cluster is backend/research context, not free-form selector authority.
        item["theme_cluster"] = card_by_id[sid].get("theme_cluster")
        if decision == "RECOMMEND":
            recommendation_rank = raw.get("recommendation_rank")
            if not isinstance(recommendation_rank, int) or isinstance(recommendation_rank, bool) or recommendation_rank <= 0:
                _invalid("GLOBAL_SELECTION_RANK_INVALID", f"invalid rank for {sid}")
            if not _nonempty(raw.get("recommendation_thesis")) or not _nonempty(raw.get("relative_advantage")):
                _invalid("GLOBAL_SELECTION_RECOMMEND_REASON_MISSING", f"missing thesis/advantage for {sid}")
            _require_traditional_text(
                raw.get("recommendation_thesis"),
                "GLOBAL_SELECTION_RECOMMEND_REASON_MISSING",
                f"recommendation thesis must be Traditional Chinese for {sid}",
            )
            _require_traditional_text(
                raw.get("relative_advantage"),
                "GLOBAL_SELECTION_RECOMMEND_REASON_MISSING",
                f"relative advantage must be Traditional Chinese for {sid}",
            )
            basis = raw.get("recommendation_basis")
            if not isinstance(basis, list) or not any(_nonempty(value) for value in basis):
                _invalid("GLOBAL_SELECTION_RECOMMEND_REASON_MISSING", f"missing basis for {sid}")
            if any(str(value).upper() not in RECOMMENDATION_BASIS_CODES for value in basis):
                _invalid("GLOBAL_SELECTION_RECOMMEND_REASON_MISSING", f"invalid basis for {sid}")
            if raw.get("selection_reason_code") is not None:
                _invalid("GLOBAL_SELECTION_SCHEMA_INVALID", f"RECOMMEND has not-selected fields: {sid}")
            if not _nonempty(raw.get("selection_reason")):
                _invalid("GLOBAL_SELECTION_RECOMMEND_REASON_MISSING", f"missing selection reason for {sid}")
            _require_traditional_text(
                raw.get("selection_reason"),
                "GLOBAL_SELECTION_RECOMMEND_REASON_MISSING",
                f"selection reason must be Traditional Chinese for {sid}",
            )
            if raw.get("distinct_thesis") is not True:
                _invalid("GLOBAL_SELECTION_RECOMMEND_REASON_MISSING", f"distinct_thesis must be true for {sid}")
            recommend_ranks.append(recommendation_rank)
        else:
            if raw.get("recommendation_rank") is not None:
                _invalid("GLOBAL_SELECTION_RANK_INVALID", f"NOT_SELECTED rank must be null: {sid}")
            reason_code = str(raw.get("selection_reason_code") or "").upper()
            if reason_code not in NOT_SELECTED_REASON_CODES:
                _invalid("GLOBAL_SELECTION_REASON_CODE_INVALID", f"invalid reason code for {sid}")
            if not _nonempty(raw.get("selection_reason")):
                _invalid("GLOBAL_SELECTION_REASON_MISSING", f"missing reason for {sid}")
            _require_traditional_text(
                raw.get("selection_reason"),
                "GLOBAL_SELECTION_REASON_MISSING",
                f"selection reason must be Traditional Chinese for {sid}",
            )
            if raw.get("veto_reason") is not None:
                _invalid("GLOBAL_SELECTION_SCHEMA_INVALID", f"NOT_SELECTED cannot have veto_reason: {sid}")
            if reason_code == "THESIS_OVERLAP":
                overlap_with = raw.get("overlap_with")
                if (
                    not isinstance(overlap_with, list)
                    or not any(str(value) in expected_ids and str(value) != sid for value in overlap_with)
                    or not _nonempty(raw.get("overlap_reason"))
                ):
                    _invalid("GLOBAL_SELECTION_OVERLAP_INVALID", f"invalid overlap evidence for {sid}")
                _require_traditional_text(
                    raw.get("overlap_reason"),
                    "GLOBAL_SELECTION_OVERLAP_INVALID",
                    f"overlap reason must be Traditional Chinese for {sid}",
                )
            not_selected_backend_ranks.append(backend_rank)
        if "theme_cluster" not in raw or not isinstance(raw.get("distinct_thesis"), bool):
            _invalid("GLOBAL_SELECTION_SCHEMA_INVALID", f"missing cluster/thesis flag for {sid}")
        normalized.append(item)

    missing = expected_ids - seen
    if missing:
        _invalid("GLOBAL_SELECTION_MISSING_STOCK", f"missing stocks: {sorted(missing)}")
    if len(items) != len(cards):
        _invalid("GLOBAL_SELECTION_ALIGNMENT_INVALID", "item count mismatch")
    if sorted(recommend_ranks) != list(range(1, len(recommend_ranks) + 1)):
        _invalid("GLOBAL_SELECTION_RANK_INVALID", "recommend ranks must be unique and continuous")

    highest_excluded_rank = min(not_selected_backend_ranks) if not_selected_backend_ranks else None
    if highest_excluded_rank is not None:
        for item in normalized:
            if (
                item["decision"] == "RECOMMEND"
                and int(item["backend_priority_rank"]) > highest_excluded_rank
            ):
                if (
                    item.get("rank_override") is not True
                    or not _nonempty(item.get("rank_override_reason"))
                    or not _nonempty(item.get("relative_advantage"))
                ):
                    _invalid(
                        "GLOBAL_SELECTION_RANK_OVERRIDE_MISSING",
                        f"rank override evidence missing for {item['stock']}",
                    )
                _require_traditional_text(
                    item.get("rank_override_reason"),
                    "GLOBAL_SELECTION_RANK_OVERRIDE_MISSING",
                    f"rank override reason must be Traditional Chinese for {item['stock']}",
                )

    recommended_count = len(recommend_ranks)
    raw_summary = payload.get("summary")
    if not isinstance(raw_summary, dict):
        _invalid("GLOBAL_SELECTION_SCHEMA_INVALID", "summary must be an object")
    # eligible/recommend/not_selected counts are derived below from `items`
    # and `cards`, not taken from the model's self-report -- they are fully
    # mechanical and the model re-counting them across the whole card set
    # added a pure arithmetic failure mode with no correctness benefit.
    if not _nonempty(raw_summary.get("selection_rationale")):
        _invalid("GLOBAL_SELECTION_SUMMARY_INVALID", "selection_rationale is required")
    _require_traditional_text(
        raw_summary.get("selection_rationale"),
        "GLOBAL_SELECTION_SUMMARY_INVALID",
        "selection_rationale must be Traditional Chinese",
    )
    return {
        "selection_version": version,
        "date": date_text,
        "selection_complete": True,
        "items": normalized,
        "summary": {
            "eligible_count": len(cards),
            "recommend_count": recommended_count,
            "not_selected_count": len(cards) - recommended_count,
            "selection_rationale": str(raw_summary["selection_rationale"])[:500],
        },
    }


def merge_selection_items(
    eligible: List[Dict[str, Any]],
    selection: Dict[str, Any],
) -> List[Dict[str, Any]]:
    by_id = {
        str(item.get("stock") or ""): item
        for item in selection.get("items", [])
        if isinstance(item, dict)
    }
    merged: List[Dict[str, Any]] = []
    for source in eligible:
        sid = str(source.get("stock") or source.get("stock_id") or "")
        selected = by_id[sid]
        merged.append(
            {
                **source,
                **selected,
                "stock": sid,
                "selection_status": selected["decision"],
                "decision": selected["decision"],
                "selection_version": selection["selection_version"],
            }
        )
    return merged


def _factual_veto_has_prerequisite(item: Dict[str, Any], veto: str) -> bool:
    if veto == "BUSINESS_MISMATCH":
        return str(item.get("business_validation") or "").upper() == "MISMATCH"
    if veto == "THEME_MISMATCH":
        return str(item.get("theme_validation") or "").upper() == "MISMATCH"
    if veto == "FALSE_SUPPLY_CHAIN_LINK":
        return str(item.get("supply_chain_validation") or "").upper() == "MISMATCH"
    # These two require an explicit factual contradiction/event summary and at least
    # one traceable source, rather than merely an enum or a generic short reason.
    evidence = item.get("veto_evidence")
    if not isinstance(evidence, dict) or not _nonempty(evidence.get("summary")):
        return False
    source_urls = evidence.get("source_urls")
    return isinstance(source_urls, list) and any(_nonempty(url) for url in source_urls)


def _quality_veto_has_prerequisite(item: Dict[str, Any], veto: str) -> bool:
    if item.get("momentum_freshness") is None or not isinstance(item.get("quality_evidence"), dict):
        return False
    quality = item.get("quality_assessment")
    if not isinstance(quality, dict):
        return False
    field = {
        "INSUFFICIENT_CONFIRMATION": "evidence_coherence",
        "MOMENTUM_NOT_FRESH": "momentum_quality",
        "WEAK_PARTICIPATION": "participation_quality",
        "CATALYST_TOO_WEAK": "catalyst_quality",
        "EVIDENCE_NOT_COHERENT": "evidence_coherence",
    }[veto]
    return str(quality.get(field) or "").upper() in {"LOW", "WEAK"}


def _derive_research_confidence(validations: Dict[str, Any]) -> str:
    values = [str(value or "").upper() for value in validations.values()]
    if values and all(value in {"VERIFIED", "NOT_APPLICABLE"} for value in values):
        return "HIGH"
    if any(value == "MISMATCH" for value in values):
        return "LOW"
    return "MEDIUM"


def _serialize_cards(cards: List[Dict[str, Any]]) -> str:
    return json.dumps(cards, ensure_ascii=False, separators=(",", ":"), default=str)


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _trim(value: Any, max_chars: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_chars] if text else None


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_traditional_text(value: Any, code: str, message: str) -> None:
    if not prompt_family.is_traditional_chinese_text(value):
        _invalid(code, message)


def _invalid(code: str, message: str) -> None:
    raise GlobalSelectionError(code, message)
