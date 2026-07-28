# Phase 3F v2 — LLM Handoff

## Canonical conclusion

**LOSER_CONTROL_ONLY**

Do not upgrade this conclusion. The only mature downtrend Winner sample is one
stock, so every downtrend Winner claim is
`INSUFFICIENT_DOWNTREND_WINNER_SAMPLE / LOW_WINNER_SAMPLE`.

## Data contract

- Research-only; production code and output were not modified.
- Continuous replay: 40 sessions, 2026-05-28 through 2026-07-24.
- Frozen comparable cohorts:
  - Dataset A: 246 matured window-first-seen episodes.
  - Dataset B: 87 matured episodes.
  - Dataset C: 140 `PENDING_FORWARD` episodes; their outcome and Day10 fields
    are intentionally null and they were excluded from all rule selection.
- Policy 0 is `RECONSTRUCTED_TOP120_FIRST_SEEN_PROXY`, not a complete frozen
  deterministic-survivor history.
- The CSV also contains 452 `RESET_GAP5_SENSITIVITY` rows. Do not add them to
  the 246/87/140 denominators.

## Numbers safe to quote

- Dataset A W/N/L = 22/186/38; Safe Rate 84.6%.
- Dataset B W/N/L = 1/47/39; Safe Rate 55.2%, Loser Rate 44.8%.
- Market Path first WEAKENING/RISK_OFF evidence after 7/2: 2026-07-08.
- Production first RISK_OFF: 2026-07-14; lag = 3 replay sessions.
- First LOW/VERY_LOW Opportunity Density after 7/2: 2026-07-07.
- Dataset B only Winner: 6416 / NARROW_LEADERSHIP.
- Dataset B CONFIRMED_POCKET Safe Rate:
  40.0%;
  NO_POCKET Safe Rate:
  46.2%.
- Policy 3 (Bundle chosen only on Dataset A = `bundle_A`):
  - Dataset B: n=31,
    Safe=80.6%,
    Loser=19.4%,
    Coverage=35.6%.
  - Dataset A: Winner Recall=
    22.7%.
  This controls B losers but removes too many A winners, which is why it is not
  a provisional regime policy.
- WATCH scan: completed=314,
  Loser Early Warning=67.1%,
  median lead=3.0,
  Winner Premature Removal=11.9%,
  Winner Retention Until Target=9.5%.
  Early warning is promising, but low retention prevents a provisional
  watchlist conclusion.

## Interpretation guardrails

1. Do not treat Dataset C as Neutral.
2. Do not call Policy 3 production-ready.
3. Do not claim CONFIRMED_POCKET worked: it did not improve Dataset B Safe Rate.
4. Do not infer that NARROW_LEADERSHIP is reliable from the sole B Winner.
5. Do not merge market-day, first-seen episode, and candidate-day rates.
6. Do not interpret the Minimal Shadow CSV as deployed; inspect
   `deployment_status`.
7. Use the report's A/B split. Dataset B was never used to choose thresholds or
   choose Bundle A/B/C.

## File map

- `phase3f_v2_report.md`: full narrative and the 20 required answers.
- `phase3f_v2_policy_comparison.csv`: Policy 0–3 and Bundle A/B/C micro/macro metrics.
- `phase3f_v2_first_seen_episodes.csv`: row-level cohort, outcome, pocket, policy flags.
- `phase3f_v2_market_path.csv`, `phase3f_v2_market_breadth.csv`,
  `phase3f_v2_opportunity_density.csv`: market-day evidence.
- `phase3f_v2_sector_pocket.csv`: primary/sub-sector daily pocket evidence.
- `phase3f_v2_candidate_day_lifecycle.csv`,
  `phase3f_v2_watchlist_actions.csv`, `phase3f_v2_barrier_outcomes.csv`:
  lifecycle and path evidence.
- `phase3f_v2_20260722_20260724_audit.csv`: formal live-list audit.
- `phase3f_v2_pending_forward.csv`: pending-only live returns.
- `phase3f_v2_minimal_shadow.csv`: non-deployed accumulation schema.
