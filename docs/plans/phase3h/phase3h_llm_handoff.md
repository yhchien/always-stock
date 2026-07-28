# Phase 3H — LLM Handoff

## Canonical conclusion

`PROVISIONAL_WINNER_ENRICHMENT`

## Numbers safe to quote

- NORMAL Dataset A: n=168, W/N/L=15/134/19, Safe=88.7%,
  Winner Dominance=10.1%, 9 dates.
- BULL_NORMAL n=14: insufficient for its own threshold; all rules use NORMAL_COMBINED.
- Best fixed policy under n>=20 and loser<=20%: P7_D3_A,
  n=25, dates=7,
  coverage=14.9%, W/N/L=
  6/18/1,
  Safe=96.0%, Dominance=25.0%.
- Formal P10 staged: n=6, Dominance=50.0%,
  promotion Winner rate=50.0%, damage=0.0%;
  it is below the n>=20/dates>=5 evidence floor and is not a positive claim.
- PIT rows=504, valid=100.0%.
- Locked evaluation: baseline n=24, Winner=1;
  P7_D3_A selected n=6 and captured
  0 Winner.  This does not positively confirm enrichment.
- Dataset C has no NORMAL episode; 140 rows remain pending in the Phase 3G
  survival domain and never enter threshold/rule selection.

## Guardrails

Do not merge Dataset B stress into this NORMAL study.  Do not quote episode
identification return as promotion return: use the promotion-close columns.
Do not treat role/confidence baselines as available.  Do not upgrade a
small-sample high Dominance row; always quote n, dates, coverage, W/N/L and
nearest Top-K.
