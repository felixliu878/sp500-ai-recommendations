# Research protocol: AI stock classifications under controlled information

## 1. Research question

When an AI classifies an S&P 500 stock as `STRONG_BUY`, `BUY`, `HOLD`, `SELL`, or `STRONG_SELL`, is it merely echoing analyst consensus, or does its classification and reasoning change when it receives distinct evidence sets?

The primary estimand is the change in the model's ordinal classification caused by adding information. The secondary estimand is agreement between the model and a normalized analyst-consensus classification.

## 2. Hypotheses

- **H1 — information sensitivity:** classifications and confidence change between Conditions A–D.
- **H2 — evidence quality:** Conditions C and D provide more dated, attributable evidence and fewer unsupported claims than A and B.
- **H3 — analyst independence:** some AI classifications differ from analyst consensus even when the model did not encounter analyst ratings.
- **H4 — stability:** repeated calls within a condition are less variable than calls across conditions.

H3 must be evaluated using the model's analyst-exposure flags and retrieved sources. A disagreement alone does not prove independent reasoning.

## 3. Experimental unit and target

The experimental unit is one `security × condition × repetition` assessment. The target is expected risk-adjusted total-return performance over 12 months relative to SPY, as of the frozen date in `config/experiment.json`.

The wording, label definitions, horizon, benchmark, schema, and model snapshot remain constant across conditions. Only the information set and permission to search the web change.

## 4. Conditions

1. **A_MEMORY_ONLY:** company identity only. No price packet, fundamentals, consensus, or web tool.
2. **B_PRICE_HISTORY:** A plus up to 24 month-end adjusted prices and monthly volume. No calculated indicators, fundamentals, consensus, or web tool.
3. **C_QUANTITATIVE:** B plus return, volatility, trend, RSI, MACD, drawdown, beta, and SPY/sector-relative features; TTM fundamentals are included when the paid data key is available. No consensus and no web tool.
4. **D_CURRENT_WEB:** C plus OpenAI web search. Retrieved sources are recorded. Analyst opinions are not supplied by the researcher, but the model must disclose whether search exposed them and whether they influenced the classification.

This is an information-ablation experiment. A is not a claim that the model has no latent training knowledge.

## 5. Universe and sampling

- **Pilot:** MU, AAPL, JPM, JNJ, NEE, and KO—six securities spanning five sectors.
- **Full run:** the then-current S&P 500 constituent-security list from the production API, archived with an official SPY holdings snapshot for auditability.
- **Benchmark:** SPY; sector-relative context uses the configured SPDR sector ETF for sectors represented in the pilot.

The name “S&P 500” does not imply exactly 500 ticker rows: multiple share classes and temporary fund-holding adjustments can change the security count. The manifest records the actual count used.

## 6. Data and feature definitions

Historical data cover approximately two calendar years through the as-of cutoff. Prices are adjusted for corporate actions when the provider supplies adjusted close.

Features are computed locally so the model cannot silently change their definition:

- total returns over 1, 3, 6, and 12 months;
- 12-month return relative to SPY and the configured sector ETF;
- annualized 20- and 60-trading-day volatility;
- SMA20, SMA50, SMA200 and percentage distance from each;
- RSI14; MACD(12,26) and 9-period signal;
- trailing-52-week maximum drawdown and range position;
- 20-day volume z-score; and
- one-year beta versus SPY.

TTM metrics and ratios are passed through from the production provider with the raw response retained. The exact fields should be frozen after inspecting the paid pilot, because large unfiltered fundamental packets can create unequal token loads.

## 7. Analyst consensus normalization

Consensus counts are mapped to the same ordinal scale:

```text
STRONG_BUY = +2, BUY = +1, HOLD = 0, SELL = -1, STRONG_SELL = -2

consensus_score =
  (2·n_strong_buy + n_buy − n_sell − 2·n_strong_sell) / total_analysts
```

Thresholds are:

- score ≥ 1.5 → `STRONG_BUY`
- 0.5 ≤ score < 1.5 → `BUY`
- −0.5 < score < 0.5 → `HOLD`
- −1.5 < score ≤ −0.5 → `SELL`
- score ≤ −1.5 → `STRONG_SELL`

This normalization is transparent but researcher-defined. Sensitivity analysis should also report the raw counts and continuous score.

## 8. Output controls

The live client requires a strict JSON schema containing the rating, confidence, standardized reason codes, exactly three thesis points, exactly three risks, cited evidence, analyst-exposure flags, and limitations. Each rendered prompt and raw response is archived.

The OpenAI integration follows the official [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs). Condition D alone follows the official [web-search tool guide](https://developers.openai.com/api/docs/guides/tools-web-search). A dated model snapshot is used to reduce drift.

The deterministic backend exists solely to validate routing and storage. Its records contain `backend=DRY_RUN_NOT_AI`, `web_search_enabled=false`, and an explicit limitation. They are not observations for H1–H4.

## 9. Analysis plan

### Primary comparisons

- Distribution of five-level ratings by condition.
- Pairwise condition flip rate.
- Mean absolute ordinal change between conditions.
- Within-condition repeat agreement and rating standard deviation.
- Confidence shift by condition.

### Analyst comparisons

- Exact five-category match.
- Mean absolute ordinal gap.
- Directional agreement (`positive`, `neutral`, `negative`).
- Weighted Cohen's kappa across securities, if all categories have sufficient observations.
- Comparison stratified by whether analyst opinions were encountered or used.

### Reasoning and evidence comparisons

- Frequency of standardized reason codes.
- Proportion of claims with a dated source.
- Number of unique sources and source-domain diversity in D.
- Manual coding of unsupported claims on a preregistered sample.
- Individual analyst-rationale comparison only if licensed report-level data are obtained.

The analyst portal's aggregate counts cannot establish whether the AI used the same reasoning as analysts.

## 10. Execution sequence

1. Run unit tests.
2. Run the six-stock dry validation and confirm all files/routes.
3. Add keys to the ignored `.env` file.
4. Run one live MU request per condition; inspect schema, web sources, token/cost footprint, and analyst fields.
5. Run the six-stock live pilot with three repetitions.
6. Freeze model, prompts, feature fields, as-of time, and full-run repetitions.
7. Run the full universe and preserve the manifest.
8. Produce condition/analyst tables; do not mix dry-run records with live results.

## 11. Portfolio follow-up

The cleanest test is prospective:

- timestamp predictions before portfolio formation;
- rebalance monthly or quarterly under a frozen rule;
- for example, equal-weight the top `BUY/STRONG_BUY` securities, with sector and maximum-position constraints specified in advance;
- compare against SPY total return and, if desired, an equal-weight S&P 500 ETF;
- include transaction costs, turnover, volatility, maximum drawdown, Sharpe ratio, and factor exposures;
- keep delisted securities and point-in-time membership.

A historical simulation generated now with today's model should be labeled exploratory, because it is vulnerable to look-ahead, model-version, and survivorship bias.

## 12. Decision requested from the professor

Approve the four-condition protocol and provide the two keys. Also decide whether the first full-universe run uses one repetition (roughly 2,000 calls) or three repetitions (roughly 6,000 calls). Three supports stability analysis; one reduces initial cost.
