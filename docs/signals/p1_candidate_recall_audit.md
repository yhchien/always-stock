# P1 Candidate Recall Audit

Audit date: 2026-07-29

## Audit

| Item | Location | Result |
|---|---|---|
| Raw union `>150 → 120` | `backend/app/signals/candidate_pool.py::build_candidate_pool` | `FOUND_AND_FIXED`: removed total truncation; the full union is deterministically ordered. |
| Phase 2 LLM input `>50 → 50` | `backend/app/signals/pipeline.py` former `_cap_llm_input` calls | `FOUND_AND_FIXED`: production uses `_order_llm_input`; compatibility wrapper ignores `limit` and keeps all candidates. |
| Other production total candidate caps | Backend signal path/config | `NOT_FOUND`: Research 8 and Decision 4 are batch sizes; B/C/D 40/20/20 are explicitly out-of-scope channel admission limits. |
| Frontend formal-data `.slice(0, 50/120)` | `frontend/src` | `NOT_FOUND`: daily signals render the complete API watchlist. |
| Prompt fixed 50/120 quantity language | Active v6 prompt and assembler | `NOT_FOUND`: no fixed-quantity claim required correction. |
| Technical failure mapped to `REMOVE` | `llm_caller._decision_fallback` | `FOUND_AND_FIXED`: fallback now has `decision=null`, `processing_status=DECISION_FAILED`; Research uses `RESEARCH_FAILED`. |
| Whole-job batch failure | `pipeline._run_parallel_batches` | `FOUND_AND_FIXED`: a failed batch is recorded and later batches continue; successful results remain available. |

## Before / after

Covered by executable regression tests:

| Scenario | Before | After |
|---|---:|---:|
| Raw union 180 | 120 | 180 |
| Raw union 100 | 100 | 100 |
| Phase 2 LLM eligible 73 | 50 | 73 |
| Research batches for 73 (`size=8`) | N/A | 10; last batch 1 |
| Decision batches for 73 (`size=4`) | N/A | 19; last batch 1 |

## Runtime behavior

- Candidate source union is explicit on every candidate through
  `candidate_sources` and `source_A/source_B/source_C/source_D`.
- Batch metadata records deterministic index, candidate IDs, timestamps, status,
  retry count (currently 0), and bounded error summary.
- A Research failure never enters Decision. A Decision failure never becomes
  `WATCH` or `REMOVE`.
- A partial run persists completed results plus technical failures and ends with
  `job.status=partial_failure`.
- Snapshot `summary.processing_summary` is optional for backward compatibility
  and records all requested funnel counts, `capacity_truncated_count=0`, batch
  audit metadata, and `is_complete`.
- No database migration was needed; `partial_failure` fits the existing
  `String(16)` job status column and processing metadata uses the existing JSON
  summary.

## Performance

Synthetic ordering plus batch construction, averaged over 1,000 iterations:

| Candidates | Avg CPU time | Research batches | Decision batches | Peak traced memory |
|---:|---:|---:|---:|---:|
| 50 | 0.0338 ms | 7 | 13 | 11.3 KiB |
| 120 | 0.0808 ms | 15 | 30 | 17.7 KiB |
| 180 | 0.1255 ms | 23 | 45 | 24.0 KiB |
| 250 | 0.1775 ms | 32 | 63 | 32.0 KiB |

The measured helper path is dominated by deterministic sorting
(`O(n log n)`); batch construction is linear. Expected request counts:

| Candidates | Research (`ceil(n/8)`) | Decision (`ceil(n/4)`) |
|---:|---:|---:|
| 50 | 7 | 13 |
| 100 | 13 | 25 |
| 200 | 25 | 50 |

This is a request-count estimate, not an API cost estimate.

## Verification

- P1 backend + signals router focused suite:
  `145 passed, 4 deselected`.
- Full backend suite with isolated SQLite:
  `1100 passed, 19 failed`; all 19 failures are the existing auth-disabled,
  rate-limit, and watchlist/trade-quality baseline. P1 introduced no new
  full-suite failure.
- P1 frontend component suite:
  `3 passed`.
- Frontend production-source TypeScript check (tests and `.next` excluded):
  passed.
- ESLint on every modified frontend source/test:
  passed.
- Full frontend suite:
  `71 passed, 18 failed`; the 18 existing failures are in unrelated
  BacktestPanel, StockList, and StockChart tests. The new P1 suite passes.
- `py_compile`, `git diff --check`, and the production cap/slice scan:
  passed.

## API and UI

- Existing `watchlist`, `removed`, and `summary` response fields remain.
- `summary.processing_summary` is optional; historical snapshots without it
  continue to render.
- The daily panel shows Raw, Phase 2 survivor, Research completed, Decision
  completed, and Unprocessed counts when metadata exists.
- `partial_failure`, `unprocessed_count > 0`, or `is_complete=false` displays
  `本次分析未完整完成`.
- No pagination was added because the production daily list already renders all
  returned WATCH decisions and no UI data cap exists.

## Out of scope

- P2 financial/ETF parity
- P3 `RECOMMEND / NOT_SELECTED / REMOVE`
- P3 Global Selector
- P3 dynamic final recommendation count
- P4 Daily Tracking Review
- P5 full Prompt v7 redesign
- P6 Final Recommendation / Tracking UI
