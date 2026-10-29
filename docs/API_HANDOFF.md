# API handoff and execution gates

## Credential

The workflow requires one professor-provided credential: `OPENAI_API_KEY`, with access to the Responses API, the configured GPT-5.4 snapshot, Structured Outputs, and web search. State Street and the provisional Yahoo/yfinance integration do not require a finance API key.

Store the key only in the Git-ignored `.env` file:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-2026-03-05
```

Never commit `.env`, print the key, place it in a command line, or copy it into an output artifact.

Yahoo/yfinance is an unofficial feasibility source, not a licensed production or historical point-in-time analyst feed. Confirm source permissions or obtain a licensed source before publication, redistribution, or commercial use.

## Gate 0 — freeze the date

Yahoo analyst fields are current snapshots. Before refreshing data, set `config/experiment.json`'s `as_of_date` to the current UTC date. The cache is accepted only when the schema, metric-set hash, cutoff, UTC retrieval date, provider-error state, and fundamental coverage match the protocol.

## Gate 1 — model-input eligibility

Every run writes `data_quality.csv`, `context_data_quality.csv`, and `acquisition_gate.json`. A paid run stops before model calls if any selected security has:

- fewer than 253 price observations;
- prices after the cutoff or more than seven days stale;
- an unresolved GICS sector or missing sector-ETF mapping;
- more than four missing requested fundamental metrics or fewer than eight packet fields;
- a Yahoo research error or cutoff mismatch; or
- invalid SPY or sector-ETF context data.

Analyst-distribution and Yahoo portal-rating coverage are recorded separately. A missing analyst baseline does not invalidate an otherwise eligible AI assessment because analyst data are withheld from Conditions A–D; the row is excluded only from the unavailable analyst-comparison statistic.

## Corrected live pilot — complete

The July 31, 2026 pilot used six stocks, four conditions, and three repetitions: 72 accepted cells. Forward P/E was excluded. The original run accepted 71 cells and rejected one Condition D response for post-cutoff evidence; an append-only retry reused the exact prompt and succeeded.

```text
outputs/pilot_2026-07-31_openai_20260731T132724294915Z/
  retries/retry_20260731T135101452966Z/
```

Key results:

- A: 0 BUY, 18 HOLD; mean confidence 26.39.
- B: 0 BUY, 18 HOLD; mean confidence 39.22.
- C: 14 BUY, 4 HOLD; mean confidence 65.22.
- D: 12 BUY, 6 HOLD; mean confidence 63.22.
- B→C changed 14 of 18 ratings; C→D changed 8 of 18.
- Mean three-run modal agreement was 91.7%.
- Every hard validation check passed; six residual primary-source URLs were manually verified.
- The 72 accepted calls cost $4.95344. All 73 paid attempts, including the rejected call and retry, cost $5.16333.

Validate the accepted directory without loading credentials:

```bash
PYTHONPATH=src python scripts/validate_pilot.py \
  outputs/pilot_2026-07-31_openai_20260731T132724294915Z/retries/retry_20260731T135101452966Z \
  --output outputs/pilot_2026-07-31_openai_20260731T132724294915Z/validation_report.json
```

The result is `PASS_WITH_WARNINGS`: all hard checks pass, while six official URLs require the documented manual trace review.

## Full-universe audit — complete

Run the acquisition and prompt pipeline without paid model calls:

```bash
PYTHONPATH=src python -m sp500_ai.cli full \
  --backend dry-run \
  --repetitions 1 \
  --refresh
```

The official SPY holdings snapshot contains 503 securities after excluding a cash row. The dry audit found 499 model-ready securities:

- HONA, Q, and FDXF have fewer than 253 observations;
- PSKY has fewer than eight fundamental packet fields;
- FISV's missing Yahoo sector was resolved as Financials with a documented current State Street XLF-holdings override; and
- the 499-security eligible dry run passed the model-input gate and completed 1,996/1,996 assessments.

In that July audit, the analyst-count baseline was available for 497 and the Yahoo portal baseline for 485. The frozen August full-run acquisition gate records coverage of 497 and 482, respectively; paired result comparisons also require an accepted D response.

## Full-scale execution — closed with documented exclusions

The approved and executed design was:

```text
499 securities × 4 conditions × 3 repetitions = 5,988 planned cells
```

The official implementation now uses OpenAI's Batch API rather than 5,988 sequential synchronous calls. The Batch API documents a 50% discount on input and output tokens. Condition D can still dominate usage because current web retrieval expands model input, and tool fees and retry behavior remain variable. The four-task smoke batch is therefore the cost and feature-compatibility check before scale-up.

The engineering gates are complete:

1. the exact 499-security universe, prompts, packets, model snapshot, schema, and 5,988 task IDs are frozen and hashed;
2. the plan is divided into one four-task MU smoke shard and 12 condition-isolated full shards of at most 500 tasks;
3. provider data and local artifacts use atomic writes and reuse valid same-cutoff caches after restart;
4. uploads recover by unique filename and size, and Batch creation recovers by frozen metadata;
5. output/error files are downloaded atomically, then reconciled by `custom_id` because Batch output order is not guaranteed;
6. every task has an independent checkpoint and at most one exact-request retry; and
7. the full phase is technically locked until explicit plan-matching approval and a passing smoke gate.

The August run and a separate recovery pass were approved and executed. The archived run is `official_full_2026-08-07_20260807T151128219298Z`, with 5,956 accepted assessments and 32 validation exclusions after three attempts per affected task. The July 31 prepared plans are historical artifacts, not the current execution status.

The original plan permitted two attempts. The recovery script requires a matching recovery-plan hash and approval before adding a third attempt with the original request settings. See [`FULL_SCALE_RESULTS.md`](./FULL_SCALE_RESULTS.md) for the final coverage, failure causes, and cost record. New data collection or paid execution requires a new same-day plan and its own approval.

The detailed operating procedure is in [`OFFICIAL_FULL_RUN_FRAMEWORK.md`](./OFFICIAL_FULL_RUN_FRAMEWORK.md).

## Pricing record

The historical pilot calculation used $2.50 per million input tokens, $15 per million output tokens, and $0.01 per web-search call. The full run used the separately recorded Batch assumptions in [`cost_summary.json`](../outputs/official_summary/cost_summary.json), producing a $243.24 lower-bound estimate from downloaded usage. These are archived assumptions, not a statement of current API prices or a reconciled invoice.
