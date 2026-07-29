"""Phase 4 Step B0: verify strategy freeze and freeze the time split.

This process uses only date, regime, and baseline-membership fields from the
replay snapshot.  It never reads the forward-return field.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "plans" / "phase4"
MANIFEST_JSON = OUTPUT_DIR / "phase4_strategy_manifest.json"
MANIFEST_SHA = OUTPUT_DIR / "phase4_strategy_manifest.sha256"
SPLIT_JSON = OUTPUT_DIR / "phase4_data_split.json"
SPLIT_SHA = OUTPUT_DIR / "phase4_data_split.sha256"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_hash(path: Path) -> str:
    return path.read_text(encoding="utf-8").split()[0]


def verify(path: Path, hash_path: Path) -> str:
    actual = sha256_file(path)
    expected = expected_hash(hash_path)
    if actual != expected:
        raise RuntimeError(
            f"hash mismatch for {path.name}: expected={expected}, actual={actual}"
        )
    return actual


def observed_bull_episodes(
    ordered_observations: list[tuple[str, str]],
    bull_enum: str,
) -> list[list[str]]:
    """Group consecutive bull observations, with any observed non-bull row as a break."""
    episodes: list[list[str]] = []
    current: list[str] = []
    for trade_date, regime in ordered_observations:
        if regime == bull_enum:
            current.append(trade_date)
        elif current:
            episodes.append(current)
            current = []
    if current:
        episodes.append(current)
    return episodes


def main() -> None:
    manifest_hash = verify(MANIFEST_JSON, MANIFEST_SHA)
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    contract = manifest["daily_cohort_contract"]
    source = Path(contract["source_snapshot"])
    if not source.exists():
        raise FileNotFoundError(
            f"{source} is missing; reconstruct it with backend/analyze_phase3d_truncation_audit.py"
        )

    # Deliberately project only non-outcome fields.  The forward-return key is
    # neither referenced nor copied by this process.
    raw: list[dict[str, Any]] = json.loads(source.read_text(encoding="utf-8"))
    date_regimes: dict[str, str] = {}
    baseline_counts: dict[str, int] = {}
    for item in raw:
        trade_date = str(item[contract["trade_date_field"]])
        regime = str(item["regime"])
        previous = date_regimes.setdefault(trade_date, regime)
        if previous != regime:
            raise RuntimeError(f"multiple regimes on {trade_date}")
        if bool(item[contract["baseline_predicate"].split()[0]]):
            baseline_counts[trade_date] = baseline_counts.get(trade_date, 0) + 1

    ordered = sorted(date_regimes.items())
    bull_enum = contract["bull_regime_enum"]
    bull_episodes = observed_bull_episodes(ordered, bull_enum)
    if not bull_episodes:
        raise RuntimeError(f"no {bull_enum} observations available")

    if len(bull_episodes) >= 2:
        development_dates = [d for episode in bull_episodes[:-1] for d in episode]
        holdout_dates = list(bull_episodes[-1])
        split_method = "EARLIER_OBSERVED_BULL_EPISODES_DEVELOPMENT_LATEST_EPISODE_HOLDOUT"
    else:
        dates = bull_episodes[0]
        cut = max(1, int(len(dates) * 0.60))
        if cut >= len(dates):
            raise RuntimeError("one bull episode has too few dates for a holdout")
        development_dates = dates[:cut]
        holdout_dates = dates[cut:]
        split_method = "SINGLE_OBSERVED_BULL_EPISODE_FIRST60_LAST40"

    split = {
        "split_version": "phase4_bull_time_split_v1",
        "frozen_on": "2026-07-29",
        "strategy_manifest_sha256": manifest_hash,
        "source_snapshot": str(source),
        "source_snapshot_sha256": sha256_file(source),
        "source_scope": (
            "Phase 3D replay sample dates; daily candidate events are not stock-deduplicated. "
            "This is a replay-date cohort, not a claim of continuous production snapshots."
        ),
        "regime_enum": bull_enum,
        "all_observed_dates_and_regimes": [
            {"trade_date": d, "market_regime": regime} for d, regime in ordered
        ],
        "bull_episodes": [
            {
                "episode_id": index,
                "dates": episode,
                "date_count": len(episode),
                "candidate_event_count": sum(baseline_counts.get(d, 0) for d in episode),
            }
            for index, episode in enumerate(bull_episodes, start=1)
        ],
        "episode_boundary_rule": (
            "Consecutive BULL_TREND observations form an observed replay episode; "
            "an observed non-BULL_TREND date closes the episode."
        ),
        "split_method": split_method,
        "development_dates": development_dates,
        "holdout_dates": holdout_dates,
        "development_date_count": len(development_dates),
        "holdout_date_count": len(holdout_dates),
        "development_candidate_event_count": sum(
            baseline_counts.get(d, 0) for d in development_dates
        ),
        "holdout_candidate_event_count": sum(
            baseline_counts.get(d, 0) for d in holdout_dates
        ),
        "candidate_count_by_bull_date": {
            d: baseline_counts.get(d, 0)
            for d, regime in ordered
            if regime == bull_enum
        },
        "outcome_access_during_split": False,
        "random_split": False,
    }
    SPLIT_JSON.write_text(
        json.dumps(split, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    digest = sha256_file(SPLIT_JSON)
    SPLIT_SHA.write_text(f"{digest}  {SPLIT_JSON.name}\n", encoding="utf-8")
    print(f"verified strategy manifest sha256={manifest_hash}")
    print(f"wrote {SPLIT_JSON.relative_to(ROOT)}")
    print(f"data split sha256={digest}")
    print(
        f"development dates={len(development_dates)}, "
        f"holdout dates={len(holdout_dates)}"
    )


if __name__ == "__main__":
    main()
