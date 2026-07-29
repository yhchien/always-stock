# P0 Selection Policy Guardrails Audit

Audit date: 2026-07-29

Scope: production backend selection/assembly, active v6 prompt and prompt
assembler, API response assembly, frontend signal views, tests, configuration,
and Phase 3/4 research scripts and reports.

The audit distinguishes final recommendation policy from the explicitly
out-of-scope upstream capacity limits (`raw union 120` and `LLM input 50`).

## Audit list

| file | symbol | current_behavior | policy_conflict | action |
|---|---|---|---|---|
| `backend/app/signals/llm_caller.py` | `MAX_FINAL_WATCHLIST_SIZE`, `_cap_final_watchlist`, `_watch_rank_key` | Dead Top-3 implementation remained beside the uncapped final assembly and could be re-enabled with one line. Production did not currently call it. | Latent fixed final Top-K contradicted the P0 guardrail. | `FOUND_AND_FIXED`: removed the constant, cap, ranking helpers, and re-enable comment. Final assembly preserves every WATCH item and its order. |
| `backend/app/signals/llm_caller.py` | `assemble_final_output` | Builds the final WATCH list from per-stock decisions without slicing it. `summary.main_hot_industries[:5]` limits only summary labels, not stocks. | No active final output conflict. | `FOUND_AND_FIXED`: retained behavior and made the no-final-Top-K policy explicit; regression test covers more than the former limit. |
| `backend/app/signals/phase2/roles.py`, `sector_cluster.py`, `pipeline_v2.py` | role/cluster classification and survivor assembly | Sector context and clusters annotate evidence/roles. No `MAX_PER_SECTOR`, `MAX_PER_THEME`, `MAX_PER_GROUP`, proportional cap, or “Nth stock” removal exists. | None found. | `NOT_FOUND`: added assembly regression coverage with eight same-industry/same-group WATCH items. |
| `backend/app/signals/candidate_pool.py`, `phase2/pipeline_v2.py` | `in_top_stocks_3d`, `in_price_momentum_pool`, `in_acceleration_pool`, `in_fundamental_pool`, `candidate_channels` | Channel flags admit candidates and appear in debug/explain trace. Phase 2 hard exclusion does not read source count or source combination. | No source-only automatic removal exists. | `NOT_FOUND`: added source A vs A+C hard-exclusion invariance test and frozen P3B policies. |
| `backend/app/signals/pipeline.py` | legacy `_llm_input_sort_key` | Legacy P1 input ordering uses `in_top_stocks_3d`/`in_top_industries_3d` before the existing input limit. Phase 2 production ordering uses numeric conviction/momentum evidence instead. | This is the existing LLM input capacity layer, not final recommendation removal. | `FOUND_BUT_OUT_OF_SCOPE`: unchanged under P1 exclusion. |
| `backend/analyze_phase3a_actionability.py` and Phase 3 reports | continuation-quality Persistence (`AT_RISK`/`FAILED`) | Research/replay only; no production import or lifecycle action was found. | None in production. | `NOT_FOUND`: frozen as `SHADOW_ONLY`; warning helper can emit `MANUAL_REVIEW` context but never a decision/action. |
| `backend/app/signals/phase2/regime_gate.py` | `FAILED_FOLLOW_THROUGH_CURRENT_EPISODE` | Existing current-cycle outcome validation (`days_since`, realized max positive/negative return) can hard-exclude that active episode. It is not the Phase 3A continuation-quality Persistence state/count experiment. | Not a P3A Persistence direct-exit connection. | `FOUND_BUT_OUT_OF_SCOPE`: retained existing Phase 2 contract and thresholds. |
| `backend/app/signals/candidate_pool.py`, `pipeline.py` | momentum ordering and capacity limits | Momentum score orders the existing raw-union 120 truncation and helps order the existing LLM input 50 capacity limit. No post-decision `momentum_rank > N => REMOVE` or final rank cutoff exists. | Upstream limits are P1; no P0 final-rank removal was found. | `FOUND_BUT_OUT_OF_SCOPE` for the two capacity limits; `NOT_FOUND` for a final Momentum Rank cutoff. Frozen P3D policy permits `RANK` only. |
| `backend/freeze_phase4_strategy.py`, `analyze_phase3*.py`, Phase 3/4 docs | source/Persistence/Top-K experiments | Contain comparators, shadow actions, rejected hypotheses, and generated research outputs. They are not imported by the production signal path. | Research labels could be unsafe only if promoted without status/usage checks. | `FOUND_AND_FIXED`: runtime-sized frozen registry and centralized status/usage helpers prevent callers from reinterpreting these results. Research scripts remain offline. |
| `backend/app/prompts/watch-list-stock-v6.md`, `llm_caller._run_decision_chunk` | active prompt policy | Already required independent per-stock decisions, but did not explicitly enumerate all P0 experimental prohibitions. | Ambiguity could let an LLM use source mix, fixed cluster count, rank cutoff, or shadow evidence as a veto. | `FOUND_AND_FIXED`: added explicit no fixed quota/cluster cap/source veto/Persistence action and evidence-status guardrails. Kept v6 because this corrects and strengthens the existing v6 authority contract; it is not the P5 v7 redesign. |
| `frontend/src/components/DailySignalsPanel.tsx` | `allSignals` | Sorts and renders the entire API `watchlist`; no formal-data `.slice(0, N)` exists. | None. | `NOT_FOUND`: no frontend change required. |
| `frontend/src/app/signals/archive/page.tsx` | `visibleActiveItems`, `TOP_N=15` | Expandable “show more” presentation of active tracking/archive rows; searching or expanding shows all data. It does not alter daily signal selection or API data. | Not a final recommendation Top-K; belongs to the tracking UI surface. | `FOUND_BUT_OUT_OF_SCOPE`: unchanged (P6/display-only). |
| `backend/app/signals/candidate_pool.py` | `POOL_SOFT_TRIGGER=150`, `POOL_HARD_LIMIT=120` | Raw union above 150 is momentum-sorted and retained to 120. | Explicit P1 item. | `FOUND_BUT_OUT_OF_SCOPE`: unchanged. |
| `backend/app/signals/pipeline.py` | `LLM_INPUT_HARD_LIMIT=50` | Caps candidates before LLM processing. | Explicit P1 item. | `FOUND_BUT_OUT_OF_SCOPE`: unchanged. |
| `backend/app/signals/momentum.py` | `CHANNEL_B_LIMIT=40`, `CHANNEL_C_LIMIT=20`, `CHANNEL_D_LIMIT=20` | Existing per-channel admission limits. | Explicitly excluded from P0. | `FOUND_BUT_OUT_OF_SCOPE`: unchanged. |

## Frozen production policy

`backend/app/signals/selection_policy.py` is the single code-level definition
for the production-relevant Phase 3 conclusions:

- `P3D_MOMENTUM_RANK_GRADIENT`: `PASS / RANK`
- `P3B_SOURCE_GROUP_AUTO_REMOVAL`: `REJECT / no usage`
- `P3B_AC_SOURCE_EFFICIENCY`: `WEAK / TIE_BREAK + SHADOW_INFORMATION`
- `P3A_PERSISTENCE_DIRECT_EXIT`: `SHADOW_ONLY / SHADOW_INFORMATION + UI_WARNING + MANUAL_REVIEW`

The helper enforces:

- only `PASS + FILTER` can auto-filter;
- only `PASS + RANK` can primary-rank;
- only `PASS` or `WEAK` with explicit `TIE_BREAK` permission can tie-break;
- `WEAK` and `SHADOW_ONLY` can never auto-filter;
- `INCOMPLETE` and `REJECT` cannot be used for any production purpose.

## P0 boundary

This change does not modify raw union 120, LLM input 50, B/C/D channel caps,
ETF/financial treatment, Phase 2 eligibility thresholds, the WATCH/REMOVE API
contract, database schema, Global Selector, three-state selection, daily
tracking lifecycle, full prompt v7, or UI funnel/tracking redesign.
