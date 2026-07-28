# Phase 3G — LLM Handoff

## Canonical conclusions

`PROVISIONAL_SURVIVAL_SIGNAL | MARKET_INCREMENT_CONFIRMED | NO_POCKET_INCREMENT | LOSER_CONTROL_ONLY`

## Numbers safe to quote

- Frozen cohorts: A=246, B=87, C=140 pending.
- P3 Survival Only B: n=36, coverage=41.4%,
  loser=25.0%, safe=75.0%, Winner count=1.
- P4 Market+Survival B: n=33, loser=18.2%,
  safe=81.8%.
- P5 Pocket+Survival B loser=25.7%; no pocket increment.
- P6 Full B loser=19.4%; it does not beat P4.
- Raw composite alert=2026-06-26 (pre-7/2 false-alarm audit);
  first post-7/2 alert=2026-07-02, first sustained alert=2026-07-07;
  false-alarm market days before 7/2=3.
- PIT watch rows=2038, valid=100.0%;
  loser warning=67.1%, median lead=2.0,
  winner premature warning=67.0%,
  winner premature removal=21.4%.
- Old lifecycle copy-pattern rows=1050/1383:
  `LIFECYCLE_OUTCOME_LEAKAGE_FOUND`.
- Available-through: prices=2026-07-27, formal WATCH=2026-07-27; no 7/28 action was fabricated.

## Guardrails

Do not claim Winner selection success: Dataset B has one Winner.  Do not treat P1
as a pure causal market factor: the locked Phase 3F publishing mask contains
survival-percentile contraction.  Pocket is descriptive unless future data show
consistent durability increment.  Dataset C and live rows did not choose rules.
Use action-date outcomes for lifecycle analysis, never copied entry outcomes.

## File map

The canonical narrative is `phase3g_report.md`.  Row-level selection is in
`phase3g_policy_matrix.csv`; aggregate attribution in
`phase3g_policy_attribution.csv`; survival raw predicates in
`phase3g_survival_predicate_trace.csv`; market evidence in the three early
transition files; pocket evidence in the activity/durability/quadrant files;
and PIT lifecycle evidence in `phase3g_watchlist_point_in_time.csv`,
`phase3g_lifecycle_outcome_leakage_audit.csv`, and the live replay.
