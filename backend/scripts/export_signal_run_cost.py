"""Export one immutable cost/funnel record for a daily-signals workflow run.

The daily snapshot is intentionally an UPSERT by target date, so it cannot be
the only audit trail when the same date is rerun. GitHub Actions uploads the
JSON produced here with a run-specific artifact name.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.models import SignalGenerationJob, SignalSnapshot


_COUNT_FIELDS = (
    "llm_eligible_count",
    "research_requested_count",
    "research_completed_count",
    "research_failed_count",
    "decision_requested_count",
    "decision_completed_count",
    "decision_failed_count",
    "global_selection_eligible_count",
    "global_selection_recommended_count",
    "global_selection_not_selected_count",
    "long_reason_completed_count",
    "tracking_review_requested_count",
    "tracking_review_completed_count",
    "tracking_review_failed_count",
)


def _int_or_none(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _job_record(job: SignalGenerationJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "status": job.status,
        "current_stage": job.current_stage,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_message": job.error_message,
    }


def _snapshot_record(snapshot: SignalSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    summary = snapshot.summary if isinstance(snapshot.summary, dict) else {}
    processing = summary.get("processing_summary")
    if not isinstance(processing, dict):
        processing = {}
    counts = {
        key: processing.get(key)
        for key in _COUNT_FIELDS
        if processing.get(key) is not None
    }
    return {
        "candidate_pool_size": snapshot.candidate_pool_size,
        "final_watchlist_size": snapshot.final_watchlist_size,
        "llm_model": snapshot.llm_model,
        "llm_total_tokens": snapshot.llm_total_tokens,
        "prompt_version": snapshot.prompt_version,
        "token_usage": processing.get("token_usage"),
        "counts": counts,
    }


def build_record() -> dict[str, Any]:
    """Build a bounded, JSON-safe record from workflow metadata and production DB."""
    target_raw = os.getenv("TARGET_DATE", "").strip()
    target_date = None
    if target_raw:
        try:
            target_date = date.fromisoformat(target_raw)
        except ValueError:
            target_date = None

    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": "daily_signals_run_cost",
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow": {
            "name": os.getenv("GITHUB_WORKFLOW"),
            "run_id": os.getenv("GITHUB_RUN_ID"),
            "run_attempt": _int_or_none(os.getenv("GITHUB_RUN_ATTEMPT")),
            "event": os.getenv("GITHUB_EVENT_NAME"),
            "sha": os.getenv("GITHUB_SHA"),
            "ref": os.getenv("GITHUB_REF"),
        },
        "target_date": target_raw or None,
        "pipeline_exit_code": _int_or_none(os.getenv("PIPELINE_EXIT")),
        "job": None,
        "snapshot": None,
    }

    if target_date is None or not os.getenv("PIPELINE_EXIT"):
        record["status"] = "pipeline_not_run"
        return record

    try:
        with SessionLocal() as db:
            job = (
                db.query(SignalGenerationJob)
                .filter(SignalGenerationJob.snapshot_date == target_date)
                .order_by(SignalGenerationJob.started_at.desc())
                .first()
            )
            snapshot = (
                db.query(SignalSnapshot)
                .filter(SignalSnapshot.snapshot_date == target_date)
                .one_or_none()
            )
            record["job"] = _job_record(job)
            record["snapshot"] = _snapshot_record(snapshot)
            record["status"] = "ok" if snapshot is not None else "no_snapshot"
    except Exception as exc:  # artifact export must not change pipeline outcome
        record["status"] = "export_error"
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]

    return record


def main() -> int:
    output_path = Path(
        os.getenv("SIGNAL_RUN_COST_OUTPUT", "artifacts/signal-run-cost.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_record(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote signal run cost record: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
