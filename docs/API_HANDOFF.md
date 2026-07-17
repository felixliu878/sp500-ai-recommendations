# API handoff and live-run checklist

The framework is ready for two professor-provided credentials:

1. **OpenAI API key** with access to the Responses API, the configured dated model snapshot, Structured Outputs, and the web-search tool.
2. **Financial Modeling Prep key** with access to these stable endpoints:
   - `sp500-constituent`
   - `historical-price-eod/dividend-adjusted`
   - `key-metrics-ttm`
   - `ratios-ttm`
   - `grades-consensus`

Store them only in the Git-ignored `.env` file:

```bash
cp .env.example .env
```

```text
OPENAI_API_KEY=...
FMP_API_KEY=...
OPENAI_MODEL=gpt-5.4-2026-03-05
```

## Gate 1 — four-call Micron smoke test

```bash
PYTHONPATH=src python -m sp500_ai.cli pilot \
  --backend openai \
  --symbols MU \
  --repetitions 1 \
  --refresh
```

Confirm before proceeding:

- four rows exist, one per condition;
- `backend` is `OPENAI_RESPONSES_API`;
- A–C have `web_search_enabled=false`;
- D has `web_search_enabled=true` and source URLs where search returned sources;
- every response passes the schema and has exactly three thesis points and risks;
- analyst counts are nonzero or the provider's absence is explicitly understood;
- token-use columns are populated; and
- no source or event is after the frozen as-of date.

## Gate 2 — 72-call live pilot

```bash
PYTHONPATH=src python -m sp500_ai.cli pilot \
  --backend openai \
  --repetitions 3 \
  --refresh
```

Review rating stability, web-source quality, consensus coverage, and total token use. If the fundamental payload is excessively large or inconsistent, freeze a smaller field whitelist before the full run.

## Gate 3 — full universe

Cheaper first pass, roughly 2,000 calls:

```bash
PYTHONPATH=src python -m sp500_ai.cli full \
  --backend openai \
  --repetitions 1 \
  --refresh
```

Stability design, roughly 6,000 calls:

```bash
PYTHONPATH=src python -m sp500_ai.cli full \
  --backend openai \
  --repetitions 3 \
  --refresh
```

The exact number is `actual constituent-security rows × 4 × repetitions` and is saved in the run manifest.
