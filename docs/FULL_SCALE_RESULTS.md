# Full-scale experiment: progress and findings

Progress reporting date: October 28, 2026. This document summarizes archived results; preparing this update did not run new paid assessments.

## Design and execution status

The study tests how information availability changes a language model's stock ratings, consistency, and similarity to analysts. The unit is one security, information condition, and independent repetition. The approved design covered 499 eligible S&P 500 securities, four conditions, and three repetitions: **5,988 planned assessments**.

| Condition | Information supplied | Accepted / planned |
|---|---|---:|
| A - Identity | Company identity, sector, and common decision task | 1,497 / 1,497 |
| B - Price history | A plus 12 monthly adjusted-close and volume observations | 1,497 / 1,497 |
| C - Quantitative | B plus technical, fundamental, SPY, and sector context | 1,497 / 1,497 |
| D - Current web | The identical C packet plus web search | 1,465 / 1,497 |

The model, five-category scale, schema, and 12-month SPY-relative decision task were held constant. The task defines what the model should evaluate; it does not provide a future return or target price. Monthly prices are the last included trading-day adjusted closes, not monthly averages; the latest month can be partial.

The frozen experiment used prompt version `v1.4.0`. The current `v1.4.1` template adds safeguards for future runs and must not be presented as the source of these archived results. Forward P/E and both analyst rating baselines were excluded from supplied model packets. D could encounter analyst opinions through web search, so exposure was audited.

## Coverage and failures

**5,956 assessments were accepted (99.47%).** The execution cycle is closed with 32 missing D results across 25 securities. The run's machine-readable state is `PERMANENT_FAILURE` and analysis status is `PARTIAL`: it exhausted its approved retry ceiling, not all tasks were accepted.

- 30 tasks failed evidence-date checks because an observation date was after the frozen cutoff.
- Two tasks failed evidence-origin rules governing when a source URL is permitted.
- All affected tasks reached three approved attempts. The original plan allowed two; a separate recovery plan authorized one more exact-request attempt.

These records are validation exclusions, not Sell recommendations or evidence that the investment predictions were wrong. Some rejected dates refer to forecast-period endpoints and need semantic review; a rejected date alone does not prove real future information was retrieved. Missingness may be systematic, so D results remain qualified by incomplete coverage.

## Main findings

### Information changes ratings

| Comparison | Matched assessments | Rating changed |
|---|---:|---:|
| A to B | 1,497 | 8.35% |
| B to C | 1,497 | 56.05% |
| C to D | 1,465 | 39.86% |
| Direct A to D | 1,465 | 45.46% (666/1,465) |

Adjacent rates cannot be added: the same assessment can change multiple times or return to its initial category. The direct A-to-D change rate is not 95%.

### Web access reduces repeatability on the common cohort

Among the same 474 securities with three accepted D results, C produced the same rating in all three repetitions for **326/474 (68.78%)**, versus **260/474 (54.85%)** for D. Approximately 45% of these securities had at least one different D rating. This statistic measures consistency, not correctness. A's very high stability is largely an information-poor baseline: every A assessment flagged insufficient evidence and 99.73% selected Hold.

### Web-enabled ratings are closer to analyst benchmarks

| Analyst baseline | Matched C-D assessments | C exact agreement | D exact agreement |
|---|---:|---:|---:|
| Count-normalized analyst rating | 1,459 | 22.34% | 29.61% |
| Yahoo portal rating | 1,417 | 18.49% | 21.67% |

The count-normalized benchmark is calculated from Yahoo analyst recommendation counts; the portal baseline is Yahoo's displayed overall rating. Each comparison uses matched stock/repetition pairs with the relevant benchmark available. The baselines remain separate, and unavailable analyst fields exclude a row only from the affected agreement statistic.

Greater agreement means greater similarity to analysts, not predictive accuracy or proof of independent reasoning. D reported encountering analyst opinions in 263/1,465 assessments (17.95%); none reported using them. Those disclosures and web audits do not establish what influenced the model internally. Analyst conventions also do not exactly match the AI's SPY-relative, risk-adjusted objective.

## Recorded cost and engineering deliverables

Downloaded usage implies a **$243.24 lower-bound cost estimate**, including available retry usage. D accounts for approximately 83.5%. This is based on archived pricing assumptions and is not a reconciled invoice or a claim about current prices; failed requests do not always contain complete usage.

The implementation includes atomic data and artifact writes, frozen request hashes, bounded parallel Batch execution, restart-safe uploads/downloads, task checkpoints, exact-request retries, an audited recovery plan, progress/ETA reporting, and aggregate analysis utilities. The local simplified workbook retains every planned task, including failed rows; it is not published with live raw artifacts.

## Next research steps

1. Finalize uncertainty estimates with stock-level dependence accounted for, and report missingness and complete-cohort sensitivity checks.
2. Confirm whether analyst comparisons remain descriptive under the SPY-relative task, or whether a separately authorized absolute-return analysis should be added.
3. Freeze the repetition-aggregation, selection, weighting, missing-data, rebalancing, transaction-cost, and prospective start-date rules before measuring portfolio performance against SPY.
4. Evaluate the archived 12-month outcomes when the horizon ends in August 2027. Live web date checks do not provide a fully historical point-in-time archive. Predictive accuracy and market outperformance have not been demonstrated.

## Evidence and artifacts

- [Progress report PDF](../output/pdf/Research_Progress_2026-10-28/Research_Progress_Report_2026-10-28.pdf) and [LaTeX source](../output/pdf/Research_Progress_2026-10-28/main.tex).
- [Shareable aggregate tables](../outputs/official_summary/), copied from the final attempt-limited analysis.
- Local source run: `official_full_2026-08-07_20260807T151128219298Z`, with frozen data cutoff August 7, 2026. The October date is the progress reporting date and does not relabel the data.
- The report's direct A-D change count, matched stability counts, and analyst denominators were independently reconciled against the accepted row-level records.

Raw responses, provider caches, credentials, approval records, task checkpoints, and full per-stock live datasets remain local. The aggregate tables are descriptive research outputs rather than individual investment advice.
