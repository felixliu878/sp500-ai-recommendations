# One-key API handoff and live-run record

The current protocol requires one professor-provided credential:

1. **OpenAI API key** with access to the Responses API, configured dated model snapshot, Structured Outputs, and web-search tool.

Yahoo market, fundamental, and analyst-benchmark data do not require a separate finance API key in this feasibility workflow.

Store the OpenAI key only in the Git-ignored `.env` file:

```bash
cp .env.example .env
```

```text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-2026-03-05
```

Never commit `.env`, print the key, place it in a command line, or copy it into an output artifact.

## Source-use boundary

The Yahoo/yfinance integration is unofficial and appropriate only for a small academic or personal-use feasibility study. It is not a licensed production feed and does not provide historical point-in-time analyst snapshots. Before redistribution, commercial use, or a publication-quality full-universe study, confirm the applicable terms and whether a licensed data source is required.

## Gate 0 — freeze the current UTC date

Yahoo analyst data are current-only. Before refreshing data, confirm the UTC date:

```bash
date -u +%F
```

Set `config/experiment.json`'s `as_of_date` to that value. A fresh Yahoo research fetch rejects any other cutoff date. Use `--refresh` for the canonical live run so every security is retrieved under the same dated protocol.

The v1.3.0 provider cache is accepted only when all of the following match:

- `yahoo-research-v2` schema version; older v1 caches remain part of the immutable pilot only;
- frozen fundamental metric-set SHA-256;
- configured cutoff;
- UTC retrieval date equal to the cutoff;
- no cached provider errors;
- no unavailable-data marker; and
- no more than four missing requested fundamental metrics.

## Gate 1 — acquisition quality before paid model calls

Every run writes `data_quality.csv`, `context_data_quality.csv`, and `acquisition_gate.json`. Every OpenAI run, pilot or full universe, aborts before any model call when:

- fewer than 253 price observations, which are required for a 252-session return;
- a price observation after the cutoff;
- price data lagging the cutoff by more than seven days;
- SPY or a required sector ETF fails the same history and freshness checks;
- a GICS sector cannot be resolved or mapped to one of the 11 sector ETFs;
- more than four missing requested fundamental metrics;
- fewer than eight compact fundamental packet fields;
- any recorded Yahoo research error;
- no current analyst rating distribution; or
- no Yahoo portal rating.

Do not proceed unless `acquisition_gate.json` reports `PASS`.

## Gate 2 — four-call Micron smoke test

```bash
PYTHONPATH=src python -m sp500_ai.cli pilot \
  --backend openai \
  --symbols MU \
  --repetitions 1 \
  --refresh
```

Confirm:

- four assessments were planned and completed with zero failures;
- `backend=OPENAI_RESPONSES_API`;
- A–C have `web_search_enabled=false`;
- D has `web_search_enabled=true`;
- every response passes the strict schema;
- every response has exactly three thesis points and three risks;
- evidence contains valid origin and date fields;
- D has captured web-call/source audit fields;
- both analyst baselines are present but absent from the model's supplied packet;
- token-use fields are populated;
- no supplied observation or cited publication is after the cutoff; and
- the manifest does not report failure-threshold abortion.

## Gate 3 — canonical v1.2.0 pilot: complete but exploratory

The pilot used six cross-sector securities, four information conditions, and three separate API repetitions:

```text
MU, AAPL, JPM, JNJ, NEE, KO
6 securities × 4 conditions × 3 repetitions = 72 final cells
```

All 39 automated tests plus 32 subtests passed. The acquisition gate passed with no issues. The configured task order was shuffled with a frozen execution seed.

The original run completed 70 cells and recorded two Condition D `JSONDecodeError` failures caused by truncated JSON. Two targeted retries reused the exact stored prompt hashes, increased `max_output_tokens` to 6,000, and both succeeded. The final consolidated directory has 72 completed cells, zero unresolved failures, and 74 total attempts. Original artifacts were not overwritten.

Canonical artifact:

```text
outputs/pilot_2026-07-24_openai_20260724T132933641207Z/
  retries/retry_20260724T140656289007Z/
```

Key final facts:

- 72 of 72 cells complete after 74 attempts;
- 831,759 recorded tokens and 107 recorded web-search calls;
- known API cost lower bound of $4.486485;
- two failed original calls have unknown usage, so exact cost is unavailable;
- 16 of 24 stock-condition groups were unanimous; mean modal agreement was 88.9%;
- 532 evidence items passed the date audit with no invalid or post-cutoff dates; and
- one D response reported encountering analyst opinions, none reported using them, and the captured web-action plus evidence/thesis/risk term audits detected no analyst terms.

See [`LIVE_PILOT_RESULTS.md`](./LIVE_PILOT_RESULTS.md) and the canonical `pilot_summary.json` for the full rating, stability, analyst, evidence, and provenance tables. Do not combine dry-run records, smoke-test records, original failed cells, or earlier prompt versions with the canonical `v1.2.0` analysis.

Post-pilot review found that v1.2.0 included Yahoo `forward_pe` in C and D. This can embed analyst forecast earnings. Direct rating baselines were withheld and the C/D comparison remains controlled, but the v1.2.0 disagreement markers do not prove zero analyst-derived influence.

## Gate 3A — credential-free artifact validation

Validate the accepted local directory without loading `.env`:

```bash
PYTHONPATH=src python scripts/validate_pilot.py \
  outputs/pilot_2026-07-24_openai_20260724T132933641207Z/retries/retry_20260724T140656289007Z \
  --output outputs/pilot_2026-07-24_openai_20260724T132933641207Z/validation_report.json
```

The canonical result is `PASS_WITH_WARNINGS`. All 72 cells, exact prompts, raw successful responses, retry provenance, C/D packet identity, condition-level web isolation, and evidence dates pass validation. Two unique official PDF URLs remain in the manual trace-review queue, and the exact API cost remains unknown because failed-call usage was unavailable.

## Gate 3B — corrected v1.3.0 pilot

The current code removes forward P/E, resolves all 11 sectors, enforces the gate for every paid run, validates response evidence before acceptance, and preserves returned failure payloads and usage when available.

After source and pilot-budget approval, run the four-call smoke test and then:

```bash
PYTHONPATH=src python -m sp500_ai.cli pilot \
  --backend openai \
  --repetitions 3 \
  --refresh
```

Keep v1.3.0 results separate from the immutable v1.2.0 pilot. Do not pool prompt versions.

## Pricing record

The cost estimate used the official [GPT-5.4 model pricing](https://developers.openai.com/api/docs/models/gpt-5.4) and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) visible for the run:

- $2.50 per million input tokens;
- $15.00 per million output tokens; and
- $0.01 per web-search call.

Known completed usage totals $4.486485. This is a lower bound because usage for the two original failed calls was not archived.

## Gate 4 — full current universe

The default Yahoo workflow uses the dated State Street SPY holdings snapshot as the actual full-run universe:

```bash
PYTHONPATH=src python -m sp500_ai.cli full \
  --backend openai \
  --repetitions 1 \
  --refresh
```

One repetition requires `actual constituent-security rows × 4` final cells. Three repetitions multiply that total by three. At 504 securities, a pilot-average extrapolation is roughly $126 for one repetition or $377 for three. These figures are not budget guarantees and exclude unknown original failure usage. Do not run the full universe until the corrected v1.3.0 pilot, Yahoo personal-use source boundary, budget, retry policy, and one-versus-three-repetition design have been approved.
