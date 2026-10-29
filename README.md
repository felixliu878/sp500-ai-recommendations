# S&P 500 AI recommendation experiment

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![Tests](https://img.shields.io/badge/tests-52%20passed-brightgreen) ![OpenAI Responses API](https://img.shields.io/badge/OpenAI-Responses%20API-412991) ![Status](https://img.shields.io/badge/status-99.47%25%20accepted-blue)

> **At a glance.** A controlled research pipeline that asks: *how does available information change an LLM's stock ratings and their consistency?* The same model rates the same S&P 500 securities under four information conditions (identity only → price history → quantitative features → live web search). Exact prompts, raw responses, data packet hashes, and retries are archived locally for audit.
>
> **Full-scale result (499 securities × 4 conditions × 3 repetitions).** The archived run accepted **5,956 of 5,988 planned assessments (99.47%)**. Adding web search changed **39.86%** of matched C–D ratings. On the same 474 securities with three accepted D results, exact three-run stability declined from **68.78% in C to 54.85% in D**. Analyst agreement increased, but predictive accuracy and market outperformance have not been established.
>
> **Skills demonstrated:** experimental design and information isolation · financial data engineering (State Street holdings, Yahoo Finance) · feature engineering with pandas/NumPy · LLM structured outputs and tool-use auditing · data-quality gates · reproducible research artifacts · automated testing.

This repository tests when an AI stock classification changes as its permitted information set expands. For each selected security, the system holds the model, 12-month SPY-relative decision target, five-level rating scale, and output schema constant while changing only the evidence available in four experimental conditions.

The current protocol uses one credential: `OPENAI_API_KEY`. State Street supplies the dated SPY holdings snapshot, while Yahoo Finance interfaces and the third-party `yfinance` package supply current market, fundamental, and analyst-benchmark data without a finance API key.

Yahoo/yfinance is an unofficial feasibility source intended for small academic and personal-use research. It is not a licensed production or point-in-time research feed. Confirm permitted use and obtain an appropriate licensed source before any commercial deployment, redistribution, or research design that requires historical analyst snapshots.

## What is implemented

- A dated State Street SPY holdings snapshot downloader.
- Yahoo price-history, compact fundamental, analyst-distribution, portal-rating, and price-target adapters.
- A strict current-day Yahoo cutoff rule and a pre-model acquisition-quality gate.
- Locally calculated return, technical, SPY-relative, and sector-relative features.
- Four versioned prompts with controlled information isolation: the archived full run uses `v1.4.0`; current next-run templates use `v1.4.1` and do not alter archived results.
- OpenAI Responses API integration with strict structured JSON output.
- Web search enabled only for Condition D, with retrieved-source and search-action audits.
- Two analyst baselines kept separate: a researcher-normalized rating distribution and Yahoo's portal rating.
- Observable markers of rating disagreement without reported analyst use; these are not treated as proof of internal reasoning.
- Raw responses, exact prompts, packet hashes, data-quality files, failure logs, comparison tables, and run manifests.
- A deterministic validation backend whose records are labeled `DRY_RUN_NOT_AI`.
- An approval-locked OpenAI Batch runner with frozen plans, bounded shards, restart-safe transfer recovery, per-task checkpoints, and exact-prompt retries.
- Fifty-two automated tests plus 39 subtests, a corrected 72-cell pilot, and the full-scale run with append-only retry provenance.
- A local progress dashboard and analysis utilities for coverage, rating changes, matched analyst agreement, stability, failures, and recorded API usage.

## Current progress and report

The full-scale execution cycle is closed after the approved recovery pass. Conditions A–C each accepted all 1,497 planned assessments; D accepted 1,465/1,497. The remaining 32 D assessments failed validation after three attempts: 30 evidence-date cutoff failures and two source-origin failures. They remain missing, not model ratings.

- [Project progress report (PDF)](output/pdf/Research_Progress_2026-10-28/Research_Progress_Report_2026-10-28.pdf)
- [Editable LaTeX source](output/pdf/Research_Progress_2026-10-28/main.tex)
- [Full-scale findings, denominators, and limitations](docs/FULL_SCALE_RESULTS.md)
- [Aggregate result tables and historical cost record](outputs/official_summary/)

The recorded usage estimate is **$243.24**, a lower bound rather than a reconciled invoice. The next research steps are to finalize uncertainty estimates, agree on the analyst-comparison target, freeze a prospective portfolio protocol, and evaluate future performance against SPY.

## Experimental conditions

| Condition | Information supplied to the model | Web search | Purpose |
|---|---|---:|---|
| A — Memory only | Company identity and sector | No | Baseline without supplied dated evidence |
| B — Price history | A plus 12 monthly adjusted-close observations and volume through each actual observation date | No | Isolate the effect of observable price movement |
| C — Quantitative | B plus locally calculated features, SPY/sector context, and a compact Yahoo fundamental packet | No | Test whether structured quantitative evidence changes the classification |
| D — Current web | C plus web search, source capture, and an as-of cutoff | Yes | Test the incremental effect of current public information |

The model never receives either analyst benchmark directly from the research pipeline. Condition D may encounter analyst opinions through web search, so the model must disclose exposure; the system separately scans D's evidence, thesis, and risks and audits captured web-search actions.

The full run uses 12 monthly observations; the earlier corrected pilot used up to 24. Prices are the last included trading-day adjusted close in each calendar month, not monthly averages. The latest month can be partial. Underlying daily histories remain available for feature calculation.

## Corrected July 31 live-pilot result (historical)

The corrected July 31, 2026 pilot is complete. Version 1.3.0 removes forward P/E because it can embed analyst earnings forecasts, resolves all 11 GICS sectors from Yahoo metadata, applies the acquisition gate to every paid run, and validates evidence semantics before acceptance. The original run produced 71 valid cells and rejected one post-cutoff evidence date; a targeted retry reused the exact archived prompt and succeeded. The consolidated artifact therefore contains all 72 planned cells after 73 paid attempts.

| Condition | BUY | HOLD | Mean confidence |
|---|---:|---:|---:|
| A — Memory only | 0 | 18 | 26.39 |
| B — Price history | 0 | 18 | 39.22 |
| C — Quantitative | 14 | 4 | 65.22 |
| D — Current web | 12 | 6 | 63.22 |

The largest shift occurred when structured quantitative data were introduced: 14 of 18 matched B-to-C ratings moved from HOLD to BUY. C and D differed in 8 of 18 matched repetitions: five changed from BUY to HOLD and three from HOLD to BUY. Web access therefore changed judgments, but did not simply make the model more bullish. Mean modal agreement across the three repetitions was 91.67%.

The corrected findings are documented in [`docs/CORRECTED_PILOT_RESULTS.md`](./docs/CORRECTED_PILOT_RESULTS.md). The detailed canonical summary and validation are generated locally in `outputs/pilot_2026-07-31_openai_20260731T132724294915Z/retries/retry_20260731T135101452966Z/`; live raw artifacts are intentionally excluded from Git. [`docs/LIVE_PILOT_RESULTS.md`](./docs/LIVE_PILOT_RESULTS.md) preserves the earlier July 24 pilot for audit history.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e '.[test]'
PYTHONPATH=src python -m pytest -q
```

For live OpenAI calls, copy the ignored template and add your OpenAI API key locally:

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

Update `as_of_date` before a new live run and use `--refresh`. A live run stops before model calls unless `acquisition_gate.json` passes all model-input checks:

- at least 253 price observations, which are needed to calculate a 252-session return;
- no price after the cutoff and no more than seven days of market-data lag;
- sufficient compact fundamental coverage;
- no Yahoo research-provider errors.

Analyst distribution and portal-rating coverage are reported separately. Missing analyst data do not invalidate an otherwise eligible AI assessment because those benchmarks are withheld from the prompt; the affected assessment is excluded only from the relevant analyst-agreement statistic.

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

The corrected pilot required 73 paid attempts because one original Condition D response used a post-cutoff evidence date and was repaired by one successful targeted retry. Retry artifacts preserve the original failure and do not overwrite the source run.

Validate an accepted local artifact without loading credentials:

```bash
PYTHONPATH=src python scripts/validate_pilot.py \
  outputs/<accepted-run-directory> \
  --output outputs/<source-run-directory>/validation_report.json
```

The corrected accepted pilot returns `PASS_WITH_WARNINGS`: every hard experiment, evidence-date, prompt, raw-response, retry, packet-isolation, and web-isolation check passes. Six cited primary-source URLs that were not automatically matched to captured web-action URLs were manually reviewed; all were dated on or before the cutoff and supported the cited claims. Usage is complete for every paid attempt, so the estimated cost is exact under the recorded rates.

Audit the full current SPY-holdings universe without paid model calls:

```bash
PYTHONPATH=src python -m sp500_ai.cli full \
  --backend dry-run \
  --repetitions 1 \
  --refresh
```

The exact call count is `eligible security rows × 4 × repetitions` and is recorded in the manifest. State Street holdings can contain more than 500 security rows because of multiple share classes. The four model-ineligible securities are now excluded by a frozen, auditable rule; they are not silently dropped.

The current snapshot contains 503 securities after excluding a cash row. The completed full-universe dry gate found 499 model-ready securities; three lack the required price history and one lacks minimum fundamental coverage. The 499-security paid design contains 5,988 assessments. Earlier cost estimates used synchronous Responses API rates; the official runner now uses the Batch API, whose documented 50% input/output discount changes that estimate. Tool usage and retries remain variable, so the four-task smoke batch is the required empirical cost check.

## Official full-run framework and recovery

The official runner uses the [OpenAI Batch API](https://developers.openai.com/api/docs/guides/batch) with the Responses endpoint. The August full run was approved and executed using this design:

```text
499 securities × 4 conditions × 3 repetitions = 5,988 tasks
4-task MU smoke batch + 5,984 remaining tasks in 12 condition-isolated shards
13 initial shards; additional retry/recovery shards preserve the original requests
```

Prepare a fresh same-day plan without contacting OpenAI:

```bash
PYTHONPATH=src python scripts/official_full_run.py prepare-current
```

If data acquisition is interrupted, run the same command again. Valid same-cutoff Yahoo caches are reused; missing, partial, or corrupt cache entries are fetched again. All cache and artifact writes are atomic.

Inspect a prepared plan:

```bash
PYTHONPATH=src python scripts/official_full_run.py preflight --run-dir official_runs/<run>
PYTHONPATH=src python scripts/official_full_run.py status --run-dir official_runs/<run>
```

For any new run, obtain approval for its exact frozen plan before recording approval or submitting requests. The first paid step is a four-task smoke batch. Those four outputs count as MU repetition 1 and are not discarded. The full shards stay locked until all four smoke outputs pass schema, identity, cutoff, evidence, and prompt-integrity validation. The default limit is two attempts per task; the archived full run had a separately approved recovery ceiling of three.

`scripts/resume_official_full_run_hotfix.py` applies an ingestion-only task-ID parser correction when loading the older frozen runtime. `scripts/recover_official_full_run.py` prepares and verifies a separately hashed recovery plan before adding one exact-request attempt. Neither script should be used to change the archived prompt or silently increase a run's budget.

The transfer layer is restart-safe at the artifact/stage level:

- an interrupted upload is recovered by the unique frozen filename and byte size;
- a created batch is recovered by plan hash, shard ID, and input-file ID;
- downloads are written atomically and a failed download is safely repeated;
- arbitrary Batch output order is mapped through unique `custom_id` values; and
- each accepted or failed task receives an independent checkpoint, so only failed tasks are retried with the same request body.

Individual HTTPS transfers do not use byte-range continuation. A failed shard transfer restarts that file, which is bounded to at most 500 requests; the current largest input is about 6.7 MB. See [`docs/OFFICIAL_FULL_RUN_FRAMEWORK.md`](./docs/OFFICIAL_FULL_RUN_FRAMEWORK.md) for the exact approval and recovery procedure.

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

The output columns make these checks explicit. In the corrected pilot, A-C had no web calls, supplied analyst ratings, or detected analyst exposure. One D assessment encountered an analyst site, disclosed the encounter, and reported that the opinion was not used. These are observable behavioral controls, not proof of hidden internal reasoning.

## Interpretation boundaries

1. Yahoo's count distribution and portal recommendation are separate analyst baselines and may disagree.
2. Neither analyst baseline shares the AI prompt's exact 12-month, risk-adjusted, SPY-relative target.
3. Aggregate ratings compare outcomes, not individual analyst reasoning. Report-level rationales would require a permitted report source.
4. “Memory only” is an information-ablation baseline, not a point-in-time historical model.
5. A retrospective portfolio made with today's model creates look-ahead and model-version leakage. The defensible performance test is prospective.
6. Current constituents create survivorship bias in historical analysis.
7. Ratings are research classifications, not personalized financial advice.

See [`docs/CORRECTED_PILOT_RESULTS.md`](./docs/CORRECTED_PILOT_RESULTS.md), [`docs/METHODOLOGY.md`](./docs/METHODOLOGY.md), [`docs/DATA_DICTIONARY.md`](./docs/DATA_DICTIONARY.md), and [`docs/API_HANDOFF.md`](./docs/API_HANDOFF.md) for the current pilot, frozen protocol, exact fields, and execution gates.

## Project map

```text
config/experiment.json       run configuration and cutoff
prompts/                     canonical condition prompts
src/sp500_ai/providers.py    State Street universe and Yahoo data adapters
src/sp500_ai/features.py     reproducible quantitative features
src/sp500_ai/llm.py          schema, OpenAI client, and dry-run validator
src/sp500_ai/pipeline.py     acquisition gate, atomic provider cache, and artifact capture
src/sp500_ai/official.py     approval-locked Batch orchestration and recovery state machine
src/sp500_ai/analysis.py     analyst baselines and condition comparisons
tests/                       fifty-two automated tests plus 39 subtests
data/raw/                    dated provider caches
outputs/                     run-specific research artifacts
outputs/official_summary/    shareable aggregate full-run findings
output/pdf/                  dated LaTeX progress reports and PDFs
scripts/                     retry, summary, and credential-free validation utilities
docs/                        protocol, dictionary, and handoff
```
