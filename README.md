# S&P 500 AI recommendation experiment

This repository tests when an AI stock classification changes as its permitted information set expands. For each selected security, the system holds the model, 12-month SPY-relative decision target, five-level rating scale, and output schema constant while changing only the evidence available in four experimental conditions.

The current protocol uses one credential: `OPENAI_API_KEY`. State Street supplies the dated SPY holdings snapshot, while Yahoo Finance interfaces and the third-party `yfinance` package supply current market, fundamental, and analyst-benchmark data without a finance API key.

Yahoo/yfinance is an unofficial feasibility source intended for small academic and personal-use research. It is not a licensed production or point-in-time research feed. Confirm permitted use and obtain an appropriate licensed source before any commercial deployment, redistribution, or research design that requires historical analyst snapshots.

## What is implemented

- A dated State Street SPY holdings snapshot downloader.
- Yahoo price-history, compact fundamental, analyst-distribution, portal-rating, and price-target adapters.
- A strict current-day Yahoo cutoff rule and a pre-model acquisition-quality gate.
- Locally calculated return, technical, SPY-relative, and sector-relative features.
- Four versioned prompts with controlled information isolation; the hardened next-run version is `v1.3.0`.
- OpenAI Responses API integration with strict structured JSON output.
- Web search enabled only for Condition D, with retrieved-source and search-action audits.
- Two analyst baselines kept separate: a researcher-normalized rating distribution and Yahoo's portal rating.
- Observable markers of rating disagreement without reported analyst use; these are not treated as proof of internal reasoning.
- Raw responses, exact prompts, packet hashes, data-quality files, failure logs, comparison tables, and run manifests.
- A deterministic validation backend whose records are labeled `DRY_RUN_NOT_AI`.
- Thirty-nine automated tests plus 32 subtests and a completed 72-cell, six-stock live pilot with append-only retry provenance.

## Experimental conditions

| Condition | Information supplied to the model | Web search | Purpose |
|---|---|---:|---|
| A — Memory only | Company identity and sector | No | Baseline without supplied dated evidence |
| B — Price history | A plus up to 24 monthly adjusted prices and volume through each actual observation date | No | Isolate the effect of observable price movement |
| C — Quantitative | B plus locally calculated features, SPY/sector context, and a compact Yahoo fundamental packet | No | Test whether structured quantitative evidence changes the classification |
| D — Current web | C plus web search, source capture, and an as-of cutoff | Yes | Test the incremental effect of current public information |

The model never receives either analyst benchmark directly from the research pipeline. Condition D may encounter analyst opinions through web search, so the model must disclose exposure; the system separately scans D's evidence, thesis, and risks and audits captured web-search actions.

## Canonical live-pilot result

The canonical prompt-v1.2.0 pilot is complete. The original run produced 70 successful cells and two truncated JSON failures; targeted retries of those exact two cells succeeded. The consolidated artifact therefore contains all 72 planned cells after 74 total API attempts.

Post-pilot review found that v1.2.0 supplied Yahoo's forward P/E in C and D. That field can incorporate analyst earnings forecasts. The direct Yahoo rating baselines were still withheld, but the pilot cannot support a strict “no analyst-derived input” interpretation. Version 1.3.0 removes forward P/E, resolves all 11 GICS sectors from Yahoo metadata, applies the acquisition gate to every paid run, and validates evidence semantics before acceptance. Run a corrected v1.3.0 six-stock pilot before scaling to the full universe.

| Condition | BUY | HOLD | Mean confidence |
|---|---:|---:|---:|
| A — Memory only | 0 | 18 | 27.39 |
| B — Price history | 1 | 17 | 39.39 |
| C — Quantitative | 13 | 5 | 66.00 |
| D — Current web | 12 | 6 | 63.78 |

The largest shift occurred when structured quantitative data were introduced. C and D still differed in 9 of 18 matched repetitions: five changed from Buy to Hold and four from Hold to Buy. Web access therefore changed judgments, but did not simply make the model more bullish.

The consolidated results, evidence audit, stability table, analyst comparisons, retry history, and cost limitations are documented in [`docs/LIVE_PILOT_RESULTS.md`](./docs/LIVE_PILOT_RESULTS.md). Sanitized machine-readable artifacts are tracked at [`outputs/LIVE_PILOT_SUMMARY.json`](./outputs/LIVE_PILOT_SUMMARY.json) and [`outputs/LIVE_PILOT_VALIDATION.json`](./outputs/LIVE_PILOT_VALIDATION.json). The detailed canonical summary is generated locally at `outputs/pilot_2026-07-24_openai_20260724T132933641207Z/retries/retry_20260724T140656289007Z/pilot_summary.json`; live raw artifacts are intentionally excluded from Git.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e '.[test]'
PYTHONPATH=src python -m pytest -q
```

For live OpenAI calls, copy the ignored template and add the professor-provided key locally:

```bash
cp .env.example .env
# Edit .env; never commit it.
```

The current workflow does not accept or require `FMP_API_KEY`; the undated FMP path is disabled for this cutoff-controlled protocol.

## Same-day Yahoo cutoff requirement

Yahoo analyst data are current snapshots rather than historical point-in-time observations. Therefore, a fresh Yahoo research fetch is permitted only when `config/experiment.json`'s `as_of_date` equals the UTC retrieval date:

```bash
date -u +%F
```

Update `as_of_date` before a new live run and use `--refresh`. A live pilot stops before model calls unless `acquisition_gate.json` passes all required checks:

- at least 253 price observations, which are needed to calculate a 252-session return;
- no price after the cutoff and no more than seven days of market-data lag;
- sufficient compact fundamental coverage;
- no Yahoo research-provider errors;
- a nonempty current analyst rating distribution; and
- an available Yahoo portal rating.

The cache is also rejected when its schema version, metric-set hash, cutoff, retrieval date, error state, or coverage does not match the frozen run.

## Validate without calling an AI model

The dry-run backend still downloads or loads Yahoo research data, computes features, renders prompts, validates the schema, and writes comparison artifacts. It does not call an AI model or execute web search.

```bash
PYTHONPATH=src python -m sp500_ai.cli pilot \
  --backend dry-run \
  --repetitions 3 \
  --refresh
```

Dry-run classifications come from a fixed routing rule and must not be interpreted as AI evidence or investment advice.

## Run a new same-day smoke test and pilot

These commands launch a new experiment; they do not recreate the archived July 24 API responses. Because Yahoo analyst fields are current snapshots, first update the configured cutoff to the current UTC date. Exact reproduction of the original pilot requires its frozen local artifacts, which are intentionally excluded from Git.

Four-call Micron smoke test:

```bash
PYTHONPATH=src python -m sp500_ai.cli pilot \
  --backend openai \
  --symbols MU \
  --repetitions 1 \
  --refresh
```

Canonical six-stock pilot command:

```bash
PYTHONPATH=src python -m sp500_ai.cli pilot \
  --backend openai \
  --repetitions 3 \
  --refresh
```

The canonical design contains exactly 72 final assessment cells:

```text
6 securities × 4 conditions × 3 repetitions = 72 cells
```

The completed pilot required 74 attempts because two original Condition D responses ended in truncated JSON and were repaired by two successful targeted retries. Retry artifacts preserve the original failures and do not overwrite the source run.

Validate an accepted local artifact without loading credentials:

```bash
PYTHONPATH=src python scripts/validate_pilot.py \
  outputs/<accepted-run-directory> \
  --output outputs/<source-run-directory>/validation_report.json
```

The canonical accepted pilot returns `PASS_WITH_WARNINGS`: the experiment, evidence-date, prompt, raw-response, retry, packet-isolation, and web-isolation checks pass; two official PDF URLs still require manual trace review, and exact cost remains unavailable for two failed original calls.

Full current SPY-holdings universe, after the corrected v1.3.0 pilot and budget approval:

```bash
PYTHONPATH=src python -m sp500_ai.cli full \
  --backend openai \
  --repetitions 1 \
  --refresh
```

The exact call count is `constituent-security rows × 4 × repetitions` and is recorded in the manifest. State Street holdings can contain more than 500 security rows because of multiple share classes or temporary fund-holding adjustments.

Using the pilot's known lower-bound cost per completed cell, a 504-security run is roughly $126 for one repetition or $377 for three repetitions. These are planning estimates, not budget guarantees: two failed pilot calls have unknown usage, prices can change, security counts can change, and a full run may have a different search and retry profile.

## Data sources and reproducibility

- **Universe:** the official [State Street SPY daily holdings workbook](https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx), saved unmodified with retrieval metadata.
- **Prices:** Yahoo's chart response, cached with raw JSON and metadata.
- **Fundamentals and analyst benchmarks:** Yahoo Finance interfaces accessed through direct requests and pinned `yfinance==1.5.2`. The compact metric set, schema version, retrieval time, cutoff, errors, and raw response are archived.
- **AI output:** the OpenAI Responses API follows the official [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) format. Only Condition D attaches [web search](https://developers.openai.com/api/docs/guides/tools-web-search).
- **Model and cost references:** the configured dated snapshot is `gpt-5.4-2026-03-05`, overridable through `OPENAI_MODEL`. Cost assumptions are linked to the official [GPT-5.4 model page](https://developers.openai.com/api/docs/models/gpt-5.4) and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing).

Yahoo fields can change, disappear, be rate-limited, or use methodologies that are not fully documented. The current implementation deliberately preserves missingness and errors instead of silently fabricating values.

## How “AI judgment” is operationalized

The experiment can observe behavior, not inspect the model's private reasoning process. Evidence consistent with additional AI judgment includes:

- rating changes when the permitted information set changes;
- disagreement with either analyst baseline when analyst opinions were not reported as used;
- medium or high evidence sufficiency;
- a rating that was not forced by insufficient data;
- no analyst terms detected in the model's final evidence; and
- for Condition D, no analyst terms detected in captured web-search actions.

The output columns make these checks explicit. In the completed v1.2.0 pilot, a positive marker means disagreement without direct rating-baseline exposure under the recorded screen. It is not proof of independent judgment, particularly because forward P/E was an analyst-derived input. The v1.3.0 rerun removes that field.

## Interpretation boundaries

1. Yahoo's count distribution and portal recommendation are separate analyst baselines and may disagree.
2. Neither analyst baseline shares the AI prompt's exact 12-month, risk-adjusted, SPY-relative target.
3. Aggregate ratings compare outcomes, not individual analyst reasoning. Report-level rationales would require a permitted report source.
4. “Memory only” is an information-ablation baseline, not a point-in-time historical model.
5. A retrospective portfolio made with today's model creates look-ahead and model-version leakage. The defensible performance test is prospective.
6. Current constituents create survivorship bias in historical analysis.
7. Ratings are research classifications, not personalized financial advice.

See [`docs/LIVE_PILOT_RESULTS.md`](./docs/LIVE_PILOT_RESULTS.md), [`docs/METHODOLOGY.md`](./docs/METHODOLOGY.md), [`docs/DATA_DICTIONARY.md`](./docs/DATA_DICTIONARY.md), and [`docs/API_HANDOFF.md`](./docs/API_HANDOFF.md) for the final pilot, frozen protocol, exact fields, and execution gates.

## Project map

```text
config/experiment.json       run configuration and cutoff
prompts/                     canonical condition prompts
src/sp500_ai/providers.py    State Street universe and Yahoo data adapters
src/sp500_ai/features.py     reproducible quantitative features
src/sp500_ai/llm.py          schema, OpenAI client, and dry-run validator
src/sp500_ai/pipeline.py     acquisition gate, orchestration, and artifact capture
src/sp500_ai/analysis.py     analyst baselines and condition comparisons
tests/                       thirty-nine automated tests plus 32 subtests
data/raw/                    dated provider caches
outputs/                     run-specific research artifacts
scripts/                     retry, summary, and credential-free validation utilities
docs/                        protocol, dictionary, and handoff
```
