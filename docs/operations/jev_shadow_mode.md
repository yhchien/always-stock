# Jev Shadow Mode

Jev is integrated as a non-decisioning P3 evidence sidecar. The existing
research stage obtains the news/company evidence first; Jev classifies that
evidence before the existing explanation and global-selection stages. Jev
does not perform web search, replace the existing LLM verification, alter the
P3 recommendation list, or alter P4 tracking and stop rules.

## Configuration

Set the Vercel AI Gateway key in the deployment secret/environment:

```bash
AI_GATEWAY_API_KEY=...
JEV_MODE=shadow
```

For GitHub Actions, add repository variable `JEV_MODE=shadow`. The daily
signals workflow defaults the variable to `off`, so enabling it is explicit
and does not unexpectedly create Jev API spend.

`JEV_MODE` values:

- `off` (default): no Jev request and no Jev evidence.
- `shadow`: run Jev and record independent evidence/comparison data.
- `assist`: reserved for a future experiment; currently remains non-decisioning and makes no request.

Optional controls are `JEV_MODEL`, `JEV_TIMEOUT_SECONDS`, `JEV_MAX_RETRIES`,
`JEV_RETRY_BACKOFF_SECONDS`, `JEV_CONCURRENCY`, and `JEV_MAX_API_CALLS`.

## Request and response

The client uses the Vercel native evaluation endpoint:

```json
{
  "model": "typesafe-ai/jev",
  "state": {
    "analysis_date": "2026-09-23",
    "stock": {},
    "thesis": {},
    "news": {},
    "prior_evidence": []
  },
  "questions": {
    "business_relevance": {
      "type": "choice",
      "instructions": "...",
      "criteria": {
        "DIRECT": "...",
        "INDIRECT": "...",
        "UNRELATED": "...",
        "UNKNOWN": "..."
      }
    }
  }
}
```

One request carries five typed choice questions: `business_relevance`,
`event_type`, `catalyst_state`, `thesis_impact`, and `verification_need`.
The normalized evidence keeps the selected classification separate from the
Jev probability distribution.

Example evidence row:

```json
{
  "schema_version": "jev_evidence_v1",
  "status": "OK",
  "provider": "vercel_ai_gateway",
  "model": "typesafe-ai/jev",
  "stock_id": "2330",
  "news_id": "a1b2c3...",
  "source_url": "https://example.com/news",
  "source_published_at": "2026-09-22",
  "business_relevance": "DIRECT",
  "event_type": "NEW_ORDER",
  "catalyst_state": "NEW",
  "thesis_impact": "SUPPORT",
  "verification_need": "NORMAL",
  "answer_confidence": {
    "business_relevance": {
      "probability": 0.92,
      "probabilities": {"DIRECT": 0.92, "INDIRECT": 0.06, "UNRELATED": 0.01, "UNKNOWN": 0.01}
    }
  },
  "usage": {"inputTokens": 331, "outputTokens": 21}
}
```

## Evidence and comparison report

After a successful daily run, inspect:

```text
GET /api/signals/snapshot/YYYY-MM-DD
```

The report is at `data.summary.jev_shadow`. It contains per-news evidence,
source URL/date, model/question versions, status, input hash, historical
evidence version, usage, latency, cache hits, API failures, and a comparison
against the original LLM's `business_validation`, `theme_validation`, and
`catalyst_status`.

Successful evaluations are cached in the additive `jev_evaluation_cache`
table. The cache key includes stock, source content/title/date/URL, original
thesis, prior-evidence version, question version, and model version; a URL by
itself is never sufficient for reuse.

An API error, invalid response, missing evidence, future-dated source, or
missing key is recorded explicitly. Such a row uses `UNKNOWN`/`UNCLEAR` only
as a failure-safe display value and is never fed into P3/P4 decisions.

## Tests

Run the unit and pipeline tests from `backend/`:

```bash
python3 -m pytest tests/test_jev_shadow.py tests/test_jev_client.py tests/test_signals_pipeline.py -q
```

Tests mock the Gateway and never require a real key. The separate
`.github/workflows/jev_smoke_test.yml` workflow remains the real API smoke
test.
