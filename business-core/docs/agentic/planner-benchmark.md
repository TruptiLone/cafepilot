# Comparing Spark and Wonder locally

This harness compares `muse-spark-1.3` with `muse-wonder-1.0` on ten café planning requests.
It calls Meta Model API directly. It does not change the application's model setting,
import the executor, run proposed actions, seed/reset data, deploy, or push changes.

## Run

Use a working Python 3.13 environment with `business-core/requirements.txt` installed.
From `business-core/`, with the existing local PostgreSQL database running and
`MODEL_API_KEY` configured in `.env`:

```bash
python -m pytest -q tests/test_planner_benchmark.py
PYTHONPATH=. python scripts/compare_planners.py
```

The default output is a new timestamped directory in `var/benchmarks/`. This directory
is ignored by Git. An explicit `--output` must name a directory that does not yet exist.
API keys are never written to the results. The harness refuses non-local database hosts
and API endpoints other than `https://api.meta.ai/v1`.

## Method

- The café catalog, staffing references, policies, and events are read in a PostgreSQL
  repeatable-read, read-only transaction. The transaction is rolled back after the snapshot.
- Both the knowledge cutoff and the café's current time are frozen to
  October 29, 2026, 18:00 America/Los_Angeles. This makes the seeded Halloween/market
  scenarios and the November 1 daylight-saving boundary reproducible in the saved inputs.
- Both models receive identical production planner system prompts, action schemas,
  reference context, and query text. First-request SHA-256 fingerprints are recorded.
- Generation: temperature 0, high reasoning effort, 16,384 output-token limit (including
  internal reasoning), 120-second timeout by default, and zero SDK HTTP retries.
- Calls run sequentially. The first model alternates between queries.
- There is one initial call per model/query: 20 initial calls. An invalid, unparseable,
  or truncated response gets at most one correction attempt with validator/parser feedback.
  Transport errors do not trigger a correction. At most 40 generation calls are made.
- `initial_api_ms` measures the first request through receipt of the response, including
  network and server time. `total_api_ms` sums all attempts. `total_wall_ms` also includes
  local validation and saving attempts. An API failure's elapsed time is not a completed plan.
- Every attempt is saved before another request is made, and reports are updated after
  every completed model/query run. Failed responses remain visible.

## Review artifacts

- `report.html`: full plans side by side, expandable attempts/validation, and differences.
- `report.md`: timing summary, complete final plans, and automatic comparisons.
- `results.json`: all measurements, model responses, usage, plans, and validation results.
- `attempts/`: per-call raw responses, exact input messages, failures, and timings.
- `inputs/`, `context.json`, `cases.json`, `manifest.json`: frozen inputs and methodology.
- `review.md`: written after reviewing the actual results, separate from automated validation.

## Limits of the comparison

This is one sample per query/model, not a statistical latency benchmark. Repeated calls
may benefit from prompt caching, and backend load changes over time. Both raw usage and
cached-token details, if supplied by the API, remain in each raw response.

Validator acceptance is not proof that user requirements are satisfied. Tools are not
executed, so actual stock shortfalls, employee conflicts, supplier acceptance, and referenced
order contents are not resolved. Q03 uses an explicitly synthetic order ID, and Q04 describes
a hypothetical staff rejection. These evaluate how the model plans reads and dependencies,
not whether those hypothetical events exist in the database.

Clarification or an out-of-scope answer is recorded separately from an invalid plan. The
review must decide whether each is appropriate for the request. Automatic argument diffs
normalize node IDs and match repeated actions by occurrence; they are aids to inspection,
not semantic equivalence scores. The reports preserve assumptions, reasons, approval
conditions, raw plans, and validator-derived edges for human review.
