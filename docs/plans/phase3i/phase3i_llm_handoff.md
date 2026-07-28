# Phase 3I — LLM Handoff

## Canonical decision

`BOTH_BRANCHES_PENDING_SAMPLE`

This is a sample-pending decision, not PASS and not FAIL.

## Safe numbers to quote

- Frozen config hash: `a1b9b6306a17bb9b089c2695ac7dbdef29e6455d76827ec96614000f578d98a0`.
- Rule replay mismatch: Branch N D0-A=0, D1-C=0; Branch R P4=0.
- Independent Branch N: eligible=0, Day1-C pass=0, Promotion W/N/L=0/0/0.
- Branch R Existing Pending: baseline total=140,
  matured=18, matured Loser=6.
- Branch R R1: selected total=29, matured selected=
  5, selected dates=6,
  matured dates=1, W/N/L=
  1/4/0.
- Prospective after 2026-07-27: zero actual ingested trading dates.
- 7/27 row-level Phase2 universe is unavailable and was not reconstructed from
  the 24-stock formal WATCH list.

## Interpretation guardrails

Do not merge Phase 3G/3H historical discovery samples into Phase 3I independent
validation. Do not treat pending as Neutral. Do not quote the current risk
rates without `EARLY_READ_ONLY` and the one-matured-date limitation. Do not
change either frozen predicate. Do not propose Phase 3J.
