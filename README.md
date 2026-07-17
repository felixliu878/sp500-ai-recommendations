# S&P 500 AI recommendation experiment

This repository is a ready-to-run research framework for testing when an AI stock classification changes as its information set expands. It compares four controlled conditions for every selected stock, then compares the AI's five-level classification with an external analyst consensus.

The code is complete and the no-key pilot has been executed. Live AI and analyst results are intentionally not fabricated: they require the `OPENAI_API_KEY` and `FMP_API_KEY` that the professor offered to provide.

## What is delivered

- A dated S&P 500/SPY holdings snapshot downloader.
- Price, technical-feature, fundamental, and analyst-consensus adapters.
- Four versioned prompts with strict information isolation.
- OpenAI Responses API integration with strict structured JSON output.
- Web search enabled only in Condition D.
- A deterministic no-key validation backend whose every output is labeled `DRY_RUN_NOT_AI`.
- Raw-response, prompt, feature, recommendation, analyst, condition-change, and run-manifest files.
- Unit tests and a six-stock cross-sector pilot.
- A research protocol, data dictionary, presentation, and word-for-word weekly meeting script.

## Experimental conditions

| Condition | Information supplied to the model | Web search | Purpose |
|---|---|---:|---|
| A — Memory only | Company identity and common decision instructions | No | Baseline: what the model says without dated evidence |
| B — Price history | A plus 24 monthly adjusted prices and volume | No | Isolate the effect of observable price movement |
| C — Quantitative | B plus technical/return features, SPY/sector context, and fundamentals when available | No | Test whether structured evidence changes the classification |
| D — Current web | C plus web search, with an as-of cutoff and source capture | Yes | Test the incremental effect of current public information |

All conditions use the same five labels and 12-month, SPY-relative decision target. The prompt files are in [`prompts`](./prompts), and their exact version is stored with each output.

## Run it now without keys

The dry run downloads a dated State Street holdings snapshot and historical prices, computes features, renders all prompts, validates the output schema, and writes 72 pilot records (6 stocks × 4 conditions × 3 repetitions). It does **not** call an AI model, search the web, or obtain analyst ratings.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m sp500_ai.cli pilot --backend dry-run --repetitions 3
```

The completed pilot is under [`outputs/pilot_2026-07-17_dry_run`](./outputs/pilot_2026-07-17_dry_run). Do not interpret its deterministic labels as AI evidence or investment advice.

## Run the live pilot when the keys arrive

Copy the template and insert the professor-provided keys locally. `.env` is ignored by Git.

```bash
cp .env.example .env
# Edit .env; never commit it.
PYTHONPATH=src python -m sp500_ai.cli pilot --backend openai --repetitions 3 --refresh
```

For the full universe:

```bash
PYTHONPATH=src python -m sp500_ai.cli full --backend openai --repetitions 1 --refresh
```

The actual call count is `number of constituent securities × 4 conditions × repetitions`. One full repetition is roughly 2,000 model calls; three repetitions are roughly 6,000. Start with the six-stock pilot, inspect costs and source quality, then freeze the configuration before the full run.

## API choices

- **Universe snapshot:** the official [State Street SPY daily holdings workbook](https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx) is saved with retrieval metadata. The production FMP endpoint can also supply constituent names and sectors.
- **Production market/analyst data:** [Financial Modeling Prep stable APIs](https://site.financialmodelingprep.com/developer/docs/stable) provide S&P constituents, [dividend-adjusted EOD prices](https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-dividend-adjusted), TTM metrics/ratios, and [grades consensus](https://site.financialmodelingprep.com/developer/docs/stable/grades-summary) through one provider. The no-key price adapter uses Yahoo's chart response only for prototype validation and marks it unofficial.
- **AI output:** the OpenAI Responses API uses the official [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) JSON-schema format. Only Condition D attaches the official [web search tool](https://developers.openai.com/api/docs/guides/tools-web-search), including retrieved-source capture.
- **Model:** the default is the dated snapshot `gpt-5.4-2026-03-05`, selected to reduce model drift; it is configurable through `OPENAI_MODEL`. Check the official [model catalog](https://developers.openai.com/api/docs/models) before freezing the live protocol.

## Important interpretation boundaries

1. Analyst consensus counts support a comparison of **recommendations**, not analyst **reasoning**. A valid reasoning comparison requires licensed individual analyst reports, or at least dated report headlines and rationales.
2. “Memory only” is not a point-in-time historical model. The model may contain latent older knowledge, so it is an information-ablation baseline—not a clean historical backtest.
3. A retrospective portfolio made today with today's model would introduce look-ahead and model-version leakage. The defensible next step is a preregistered forward portfolio with predictions timestamped before returns occur.
4. A current-constituent historical backtest introduces survivorship bias. Point-in-time membership is required for a historical benchmark comparison.
5. Ratings are research classifications, not personalized financial advice.

The full design and analysis plan are in [`docs/METHODOLOGY.md`](./docs/METHODOLOGY.md), and field definitions are in [`docs/DATA_DICTIONARY.md`](./docs/DATA_DICTIONARY.md).

## Project map

```text
config/experiment.json       frozen run configuration
prompts/                     canonical condition prompts
src/sp500_ai/providers.py    universe, price, fundamentals, consensus adapters
src/sp500_ai/features.py     reproducible quantitative features
src/sp500_ai/llm.py          schema, OpenAI client, dry-run validator
src/sp500_ai/pipeline.py     end-to-end orchestration and artifact capture
src/sp500_ai/analysis.py     analyst normalization and condition comparisons
tests/                       nine unit tests
outputs/                     run-specific data and presentation deliverables
docs/                        methods, dictionary, and meeting script
```
