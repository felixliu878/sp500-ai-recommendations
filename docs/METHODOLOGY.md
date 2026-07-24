# Research protocol: AI stock classifications under controlled information

## 1. Research question

When an AI classifies an S&P 500 security as `STRONG_BUY`, `BUY`, `HOLD`, `SELL`, or `STRONG_SELL`, is it merely reproducing an analyst consensus, or does its observable classification and evidence change as its permitted information set expands?

The primary estimand is the change in the model's ordinal classification caused by adding information. Secondary estimands are agreement with two external analyst baselines and the frequency of observable behavior consistent with judgment that is not explained by reported analyst use.

## 2. Hypotheses

- **H1 — information sensitivity:** classifications and confidence change between Conditions A–D.
- **H2 — evidence quality:** Conditions C and D provide more dated, attributable evidence and fewer unsupported claims than A and B.
- **H3 — observable independence:** some classifications differ from analyst baselines despite sufficient evidence, no forced rating, and no reported analyst use.
- **H4 — stability:** repeated calls within a condition are less variable than calls across conditions.

H3 concerns observable output behavior. It does not identify the model's private reasoning process or prove that analyst information had no latent influence.

## 3. Experimental unit and target

The experimental unit is one `security × condition × repetition` assessment. The target is expected risk-adjusted total-return performance over 12 months relative to SPY, as of the frozen cutoff in `config/experiment.json`.

The label definitions, target, schema, model snapshot, prompt version, and evaluation horizon remain constant. Only the permitted information set and web-search access change.

## 4. Conditions

1. **A_MEMORY_ONLY:** company identity and sector only. No supplied prices, fundamentals, consensus, or web tool.
2. **B_PRICE_HISTORY:** A plus up to 24 monthly adjusted-price observations and monthly volume through each actual observation date. No calculated indicators, fundamentals, consensus, or web tool.
3. **C_QUANTITATIVE:** B plus locally calculated return, volatility, trend, RSI, MACD, drawdown, beta, SPY/sector-relative features, and a compact Yahoo fundamental packet. No analyst benchmark and no web tool.
4. **D_CURRENT_WEB:** C plus OpenAI web search. Retrieved sources and web-search actions are recorded. Analyst opinions are not supplied by the researcher, but the model must disclose whether search exposed them and whether they influenced the classification.

This is an information-ablation experiment. Condition A is not a claim that the model has no latent training knowledge.

## 5. Universe and sampling

- **Pilot:** MU, AAPL, JPM, JNJ, NEE, and KO—six securities spanning five sectors.
- **Canonical pilot:** 6 securities × 4 conditions × 3 repetitions = 72 completed assessment cells.
- **Full run:** the current constituent-security rows in a dated State Street SPY holdings workbook.
- **Benchmark:** SPY; sector-relative context uses the configured SPDR sector ETF when available.

The name “S&P 500” does not imply exactly 500 ticker rows. Multiple share classes and temporary fund-holding adjustments can affect the security count. The exact evaluated universe is archived.

## 6. Data sources and permitted-use boundary

State Street supplies the official SPY holdings workbook. Yahoo's chart interface supplies adjusted daily price history. Yahoo Finance interfaces and pinned `yfinance==1.5.2` supply current fundamentals, recommendation distributions, portal summary fields, and analyst price targets.

Yahoo/yfinance is unofficial and is used only for a small academic or personal-use feasibility study. It is not a licensed production or historical point-in-time research feed. Yahoo interfaces can change, be rate-limited, or omit fields, and their methodologies may not be fully documented. The source must be reconsidered before commercial use, redistribution, a historical analyst study, or a publication-quality full-universe run.

## 7. Strict current-day cutoff and acquisition gate

Yahoo analyst fields are current snapshots. A fresh Yahoo research fetch is permitted only when the configured cutoff equals the UTC retrieval date. This prevents a current analyst snapshot from being mislabeled as an older point-in-time observation.

The Yahoo research cache is valid only when its schema version, fundamental metric-set hash, configured cutoff, and UTC retrieval date match and it contains no provider errors or excessive missingness.

Before any paid OpenAI model call, including pilot and full-universe scopes, the acquisition gate requires:

- at least 253 price observations, sufficient for a 252-session return;
- no price after the cutoff;
- no more than seven days between the latest price and cutoff;
- no more than four missing requested fundamental metrics;
- at least eight compact fundamental packet fields;
- no research-provider errors;
- a nonempty current analyst rating distribution; and
- an available Yahoo portal rating.

The gate result and security-level measurements are saved in `acquisition_gate.json` and `data_quality.csv`. A live pilot aborts before OpenAI calls if the gate fails.

## 8. Market features and fundamental packet

Historical prices cover approximately two calendar years through the cutoff. Features are computed locally:

- total returns over 1, 3, 6, and 12 months;
- 12-month return relative to SPY and the configured sector ETF;
- annualized 20- and 60-trading-day volatility;
- SMA20, SMA50, SMA200 and percentage distance from each;
- RSI14; MACD(12,26) and its 9-period signal;
- trailing-52-week maximum drawdown and range position;
- 20-day volume z-score; and
- one-year beta versus SPY.

The hardened v1.3.0 compact fundamental packet can include:

- market capitalization and trailing P/E;
- TTM revenue, net income, diluted EPS, free cash flow, operating income, and operating cash flow;
- latest-quarter debt, equity, and cash plus short-term investments; and
- locally derived operating margin, net margin, free-cash-flow margin, debt-to-equity, and net cash.

Each supplied fundamental value retains its observation date, period type, currency, and data identifier when available. The provider filters observations after the cutoff, archives the raw response, and records the metric-set hash and missing metrics.

## 9. Two analyst baselines

The external analyst data are never inserted into Conditions A–D by the research pipeline. They are joined only after model assessment.

### 9.1 Count-normalized distribution

Yahoo's current `strongBuy`, `buy`, `hold`, `sell`, and `strongSell` counts are mapped onto the model's ordinal scale:

```text
STRONG_BUY = +2, BUY = +1, HOLD = 0, SELL = -1, STRONG_SELL = -2

count_normalized_score =
  (2·n_strong_buy + n_buy − n_sell − 2·n_strong_sell) / total_ratings
```

Thresholds are:

- score ≥ 1.5 → `STRONG_BUY`
- 0.5 ≤ score < 1.5 → `BUY`
- −0.5 < score < 0.5 → `HOLD`
- −1.5 < score ≤ −0.5 → `SELL`
- score ≤ −1.5 → `STRONG_SELL`

This is a transparent researcher-defined normalization.

### 9.2 Yahoo portal rating

Yahoo's portal recommendation key is mapped separately to the five-level scale and retained with its reported recommendation mean and number of opinions. The portal baseline and count-normalized baseline can differ because their source populations or methodologies may differ. The analysis must report both and must not silently choose one as ground truth.

Neither baseline shares the AI prompt's exact 12-month, risk-adjusted, SPY-relative target.

## 10. Structured output and evidence provenance

Prompt version `v1.3.0` requires a strict JSON schema containing:

- rating, confidence, and evidence sufficiency;
- whether the rating was forced by insufficient data;
- standardized reason codes;
- exactly three thesis points and three risks;
- up to 12 evidence items;
- analyst-opinion encountered/used flags; and
- limitations.

Every evidence item records a category, origin, observation date, publication date, and source URL. Origins distinguish supplied prices, supplied technical data, supplied fundamentals, primary web sources, secondary web sources, model memory, and other evidence.

Condition D also records search-call count, source count, retrieved URLs, analyst-related terms detected in web actions, and matched terms. Its structured evidence, thesis, and risks are independently scanned for analyst-related language.

For each successful assessment, the rendered prompt, input packet hash, prompt hash, raw API response, response identifier, token count, and model identifier are archived. Failed calls retain sanitized error records; unavailable provider usage is explicitly reported rather than inferred.

## 11. Operational definition of additional judgment

The system presents additional judgment as observable fields, not as a hidden-process claim. The stored rating-disagreement marker can be true only when:

- the AI rating differs from the selected analyst baseline;
- the model reports that analyst opinions were not used;
- evidence sufficiency is `MEDIUM` or `HIGH`; and
- the rating was not forced by insufficient data.

Separate markers are calculated for the count-normalized and Yahoo portal baselines. Interpretation also uses:

- `analyst_opinions_encountered`;
- `analyst_opinions_used`;
- analyst terms detected in final-output evidence;
- analyst terms detected in web-search actions;
- reason codes and evidence origins; and
- rating changes across information conditions.

A positive marker means a rating differs from a direct analyst baseline under the recorded screen. It does not prove independent judgment, validate the model's self-report, or reveal internal chain-of-thought.

The final pilot report also applies a stricter reported-exposure filter: it excludes any assessment that reported encountering analyst opinions, even if the assessment reported that those opinions were not used. This distinction matters because one Condition D assessment reported encounter but not use.

## 12. Analysis plan

### Primary comparisons

- Five-level rating distribution by condition.
- Pairwise condition flip rate.
- Mean absolute ordinal change between conditions.
- Within-condition repeat agreement and rating-score standard deviation.
- Confidence and evidence-sufficiency shifts by condition.

### Analyst comparisons

- Exact match and ordinal gap against each analyst baseline.
- Directional agreement (`positive`, `neutral`, `negative`).
- Weighted Cohen's kappa across securities when category support is sufficient.
- Disagreement-marker frequency by condition and analyst baseline.
- Stratification by reported analyst exposure and web-action audit.

### Reasoning and evidence comparisons

- Frequency of standardized reason codes and evidence origins.
- Proportion of claims with appropriate observation/publication dates.
- Unique source count and source-domain diversity in D.
- Exact matching of cited evidence URLs against URLs captured in the API response.
- Manual verification of a small residual set of official URLs that do not match captured strings exactly.
- Manual coding of unsupported claims on a preregistered sample.
- Individual analyst-rationale comparison only if a permitted report-level source is obtained.

Aggregate analyst ratings cannot establish whether the AI used the same reasoning as individual analysts.

## 13. Execution sequence

1. Run all 39 automated tests plus 32 subtests. **Complete.**
2. Set the cutoff to the current UTC date. **Complete for the July 24 pilot.**
3. Run the deterministic six-stock validation and inspect acquisition artifacts. **Complete.**
4. Run one live MU assessment per condition. **Complete.**
5. Inspect schema, dates, web sources, analyst fields, failures, and token footprint. **Complete.**
6. Run the canonical 72-cell six-stock pilot under prompt version `v1.2.0`. **Complete after targeted repair of two truncated JSON cells.**
7. Audit the design for analyst-derived inputs and full-run safety. **Complete; forward P/E and scale-up gate issues were found and corrected in v1.3.0.**
8. Produce condition, analyst, stability, evidence, and cost summaries without mixing dry-run, smoke-test, failed, or earlier prompt-version records. **Complete.**
9. Run a corrected v1.3.0 six-stock pilot without forward P/E. **Pending approval.**
10. Run the full current universe only after the corrected pilot plus source, cost, and repetition approval. **Pending.**

The live pipeline aborts after three consecutive model-call failures or six total failures.

### Targeted retry rule

The original canonical execution succeeded for 70 cells and produced two truncated JSON failures in Condition D. The retry process was append-only:

- only the two failed `symbol × condition × repetition` cells were selected;
- each retry reused the exact archived prompt SHA-256;
- retry `max_output_tokens` was raised from 4,000 to 6,000;
- both retries succeeded;
- original artifacts were not overwritten; and
- the consolidated dataset records `ORIGINAL_SUCCESS` or `TARGETED_RETRY_SUCCESS` for every final row.

This produced 72 final cells after 74 total API attempts. Usage from the two original failed calls was not archived, so cost remains a lower bound.

## 14. Canonical pilot findings

The accepted consolidated run is:

```text
outputs/pilot_2026-07-24_openai_20260724T132933641207Z/
  retries/retry_20260724T140656289007Z/
```

The rating distributions were:

- A: 0 Buy, 18 Hold; mean confidence 27.39.
- B: 1 Buy, 17 Hold; mean confidence 39.39.
- C: 13 Buy, 5 Hold; mean confidence 66.00.
- D: 12 Buy, 6 Hold; mean confidence 63.78.

The C-to-D comparison flipped in 9 of 18 matched repetitions: five Buy-to-Hold and four Hold-to-Buy. Sixteen of 24 stock-condition groups were unanimous, and mean modal agreement was 88.9%.

Against the count-normalized analyst baseline, exact match was 72.2% in C and 66.7% in D. Against Yahoo's portal baseline, exact match was 66.7% in C and 50.0% in D. Under the recorded sufficient-evidence and no-direct-rating-exposure filter, C differed from the count baseline in 5 of 18 assessments and from the portal baseline in 6 of 18. D differed in 6 of 17 and 9 of 17, respectively.

One D assessment reported encountering analyst opinions, none reported using them, and precise audits of captured web actions plus structured evidence, thesis, and risks detected no analyst terms. These findings are behavior consistent with additional judgment, not proof of independent internal reasoning.

The evidence audit covered 532 items and found no invalid or post-cutoff dates. The 97 cited web-evidence items comprised 96 primary and one secondary item, covering 45 unique URLs across 11 domains. Eighty-five citation instances matched the captured source list exactly, four more matched after conservative URL normalization, and the remaining eight instances represented two unique official issuer PDF URLs that were manually reviewed.

Recorded completed usage was 831,759 tokens plus 107 web-search calls. Using the official [GPT-5.4 model page](https://developers.openai.com/api/docs/models/gpt-5.4) and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing), known cost is at least $4.486485. A 504-security extrapolation is roughly $126 for one repetition or $377 for three, but neither figure is a budget guarantee.

The detailed result tables and limitations are in [`LIVE_PILOT_RESULTS.md`](./LIVE_PILOT_RESULTS.md).

### Post-pilot protocol correction

The v1.2.0 packet included Yahoo `forward_pe`, which can embed analyst forecast earnings. Direct rating baselines were withheld, and C/D packet identity remains valid, but a strict analyst-independent interpretation is not defensible. Version 1.3.0 removes forward P/E, requires all 11 GICS sector mappings, resolves full-universe sectors from Yahoo metadata, enforces the acquisition gate for every OpenAI run, rejects nonpositive run sizes, validates cutoff and evidence-origin semantics before accepting a response, preserves returned raw failure payloads and usage when available, and disables the non-cutoff-safe FMP execution path.

## 15. Portfolio follow-up

The cleanest performance test is prospective:

- timestamp predictions before portfolio formation;
- preregister a monthly or quarterly rebalance rule;
- define selection, weighting, sector, and maximum-position constraints;
- compare with SPY total return and, if desired, an equal-weight S&P 500 ETF;
- include transaction costs, turnover, volatility, maximum drawdown, Sharpe ratio, and factor exposure; and
- preserve delisted securities and point-in-time membership.

A historical simulation generated now with today's model is exploratory because of look-ahead, model-version, and survivorship bias.

## 16. Decisions required before scale-up

The professor should approve:

1. the four-condition protocol and two-baseline comparison;
2. Yahoo/yfinance as a provisional feasibility source or a replacement licensed source;
3. a full-universe budget that includes retry and price-change headroom; and
4. one versus three repetitions for the full-universe run.
