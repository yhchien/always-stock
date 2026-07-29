# Phase 4 Experiment Ledger

> Strategy-freeze input only. This ledger was created without loading the Phase 4 daily cohort or any Phase 4 future outcome.

## Status summary

| status | count |
|---|---:|
| PASS | 1 |
| WEAK | 6 |
| SHADOW_ONLY | 4 |
| REJECT | 6 |
| INCOMPLETE | 3 |

## Corrected chronology

| date | experiment_id | status | Phase 4 eligible | allowed usage | main result |
|---|---|---|---:|---|---|
| 2026-07-24 | P3A_PERSISTENCE_DIRECT_EXIT | SHADOW_ONLY | false | SHADOW_INFORMATION | Losers sometimes lost less, but winners lost more upside; no rule improved all left-tail thresholds. |
| 2026-07-27 | P3B_SOURCE_GROUP_AUTO_REMOVAL | REJECT | false | NONE | No source group passed the removal criteria; apparently risky groups also contained many winners. |
| 2026-07-27 | P3B_AC_SOURCE_EFFICIENCY | WEAK | false | TIE_BREAK|SHADOW_INFORMATION | A+C had the best candidates-per-winner and big-losers-per-winner, but the analysis was descriptive and non-causal. |
| 2026-07-27 | P3C_RISKOFF_FAILURE_VETO | REJECT | false | NONE | No archetype or veto was stable; some negative-looking archetypes contained many winners. |
| 2026-07-27 | P3C_CANDIDATE_DISCOVERY_RECALL | SHADOW_ONLY | false | SHADOW_INFORMATION | Most misses occurred in candidate discovery, but no safe compression rule was produced. |
| 2026-07-27 | P3D_MOMENTUM_RANK_GRADIENT | PASS | true | RANK | Winner rate declined monotonically from 31.2% in rank 1-40 to 8.2% in rank 501+; Top120 beat 121-200 by 12.0pp. |
| 2026-07-27 | P3D_FIXED_SIZE_REPLACEMENT | REJECT | false | NONE | All three simulations reduced winner count. |
| 2026-07-27 | P3D_FUNDAMENTAL_CHANNEL_D | INCOMPLETE | false | NONE | Upstream revenue coverage was incomplete; the rule could not be evaluated. |
| 2026-07-28 | P3E_RISKOFF_EXTREME_ACCELERATION_FILTER | WEAK | false | SHADOW_INFORMATION | Reached the loser-control goal but not winner dominance; short stress window and one winner. |
| 2026-07-28 | P3F_REGIME_ADAPTIVE_CONTRACTION | WEAK | false | SHADOW_INFORMATION | Policy family reduced stress losers, but winner evidence was insufficient and later attribution removed pocket increment. |
| 2026-07-28 | P3G_STOCK_SURVIVAL_GATE | WEAK | false | SHADOW_INFORMATION | B loser rate fell from 44.8% to 25.0%, but safe rate was 75.0% and A winner recall only 27.3%. |
| 2026-07-28 | P3G_MARKET_PLUS_SURVIVAL | WEAK | false | SHADOW_INFORMATION | B loser rate improved another 6.8pp to 18.2%, while A changed only from 15.2% to 16.2%. |
| 2026-07-28 | P3G_POCKET_INCREMENT | REJECT | false | NONE | Pocket+survival did not beat survival; directions were inconsistent across A/B. |
| 2026-07-28 | P3G_EARLY_MARKET_TRANSITION | REJECT | false | NONE | Persistent signal was only one session earlier; false alarms remained and B loser rate was 40%. |
| 2026-07-28 | P3G_POINT_IN_TIME_WATCHLIST | SHADOW_ONLY | false | SHADOW_INFORMATION | Warnings caught 67.1% of losers but prematurely warned 67.0% of winners; removals were also premature for 21.4% of winners. |
| 2026-07-28 | P3H_DAY0_POSITIVE_BUNDLES | REJECT | false | NONE | All Day0 bundles had loser rate above 20%; the nominally best dominance rule was not qualified. |
| 2026-07-28 | P3H_DAY1_DAY3_CONFIRMATION | WEAK | false | TIE_BREAK|SHADOW_INFORMATION | Historical Day3-A reached 25% winner dominance, but locked evaluation missed its sole winner; confirmation also incurred delay. |
| 2026-07-28 | P3H_FIXED_TOPK | SHADOW_ONLY | false | SHADOW_INFORMATION | Top-K was only a comparator; no fixed K passed a predeclared compression gate. |
| 2026-07-28 | P3I_NORMAL_FROZEN_BRANCH | INCOMPLETE | false | NONE | No independent NORMAL sample was available. |
| 2026-07-28 | P3I_RISK_FROZEN_BRANCH | INCOMPLETE | false | NONE | Early read was favorable but came from one matured date and no prospective rows. |

## Chronology resolution

- Phase 3G supersedes Phase 3F pocket attribution: pocket evidence is `REJECT`, not `WEAK`.
- Phase 3G point-in-time replay supersedes the leaked Phase 3F lifecycle interpretation; lifecycle remains `SHADOW_ONLY`.
- Phase 3I does not upgrade Phase 3H or Phase 3G: both frozen branches are `INCOMPLETE` because independent samples are absent or immature.
- The only `PASS` evidence eligible for automatic Phase 4 use is the existing momentum ordering from Phase 3D, and its allowed usage is `RANK`, never `FILTER`.

The full machine-readable fields, including cohort, effects, robustness, and source files, are in `phase4_experiment_ledger.csv`.
