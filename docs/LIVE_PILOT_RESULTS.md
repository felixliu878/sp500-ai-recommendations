# Live six-stock pilot results

> **Historical result.** This file preserves the July 24, 2026 v1.2.0 pilot for audit history. The corrected July 31 v1.3.0 result, which excludes forward P/E, is the current result in [`CORRECTED_PILOT_RESULTS.md`](./CORRECTED_PILOT_RESULTS.md).

## Executive result

The prompt-v1.2.0 pilot is complete. It contains 72 unique `security × condition × repetition` assessment cells for AAPL, JNJ, JPM, KO, MU, and NEE.

The original randomized run produced 70 valid assessments and two truncated Condition D JSON responses. An append-only retry utility reused the two exact archived prompts with a larger output allowance; both retries succeeded. The accepted dataset therefore has 72 valid cells after 74 API attempts. The original failures remain preserved.

This is a feasibility and behavior study, not investment advice and not a performance test.

## Experimental conditions

| Condition | Added information | Web |
|---|---|---:|
| A — Memory only | Company identity and sector | No |
| B — Price history | A plus dated monthly prices and volume | No |
| C — Quantitative | B plus technical, SPY/sector-relative, and fundamental data | No |
| D — Current web | The identical C packet plus current public web evidence | Yes |

The model never received the researcher’s Yahoo analyst benchmarks. Those benchmarks were joined only after each assessment.

## Post-pilot design caveat

A release audit found that the v1.2.0 C/D fundamental packet included Yahoo's `forward_pe` field. Forward P/E can incorporate consensus forecast earnings and is therefore analyst-derived, even though no Buy/Hold/Sell rating, recommendation count, portal label, or price target was supplied.

This does not invalidate the information-sensitivity or C-to-D comparisons, because C and D received the same packet and the direct analyst baselines were withheld. It does narrow the independence claim: the disagreement screens below are exploratory evidence of behavior beyond the direct rating baselines, not clean evidence of zero analyst-derived influence.

The hardened v1.3.0 protocol removes forward P/E. A corrected six-stock v1.3.0 pilot should be run before the full-universe experiment.

## Rating and confidence results

| Condition | BUY | HOLD | Mean confidence | Forced by insufficient data |
|---|---:|---:|---:|---:|
| A — Memory only | 0 | 18 | 27.39 | 18 |
| B — Price history | 1 | 17 | 39.39 | 17 |
| C — Quantitative | 13 | 5 | 66.00 | 0 |
| D — Current web | 12 | 6 | 63.78 | 0 |

No assessment returned `STRONG_BUY`, `SELL`, or `STRONG_SELL`.

The largest shift occurred when structured quantitative information was introduced. Relative to A, C changed 13 of 18 ratings, all from Hold to Buy. Current web access did not simply add optimism: C and D differed in 9 of 18 matched repetitions, with five `BUY → HOLD` changes and four `HOLD → BUY` changes. The mean signed C-to-D ordinal change was slightly negative at −0.0556.

## Modal result by stock

| Stock | A | B | C | D |
|---|---|---|---|---|
| AAPL | HOLD | HOLD | BUY | BUY |
| JNJ | HOLD | HOLD | BUY | BUY |
| JPM | HOLD | HOLD | BUY | BUY |
| KO | HOLD | HOLD | BUY | HOLD |
| MU | HOLD | HOLD | HOLD | BUY |
| NEE | HOLD | HOLD | HOLD | HOLD |

These are modal ratings across three repetitions. A modal label can hide variation: for example, AAPL and JNJ each had two Buy and one Hold result in D.

## Stability

Sixteen of the 24 stock-condition groups were unanimous across all three repetitions, and the mean modal agreement rate was 88.9%.

| Condition | Unanimous stock groups |
|---|---:|
| A | 6 of 6 |
| B | 5 of 6 |
| C | 3 of 6 |
| D | 2 of 6 |

The richer conditions produced stronger evidence and higher confidence, but not perfectly deterministic classifications. Repetition remains necessary for any full-universe research design.

## Comparison with analysts

Yahoo’s current recommendation-count distribution normalized to `BUY` for all six stocks. Yahoo’s separate portal label was `BUY` for five stocks and `STRONG_BUY` for MU. The two baselines are intentionally reported separately.

| Condition | Exact match: count-normalized | Exact match: Yahoo portal |
|---|---:|---:|
| A | 0.0% | 0.0% |
| B | 5.6% | 0.0% |
| C | 72.2% | 66.7% |
| D | 66.7% | 50.0% |

The model became more similar to analysts once it received useful quantitative information, but it did not merely reproduce either direct rating baseline. Under the recorded screen requiring medium/high evidence, no forced rating, no reported analyst use or encounter, and no analyst terms in the audited evidence, thesis, risks, or captured search actions:

- C differed from the count-normalized baseline in 5 of 18 assessments and from the portal baseline in 6 of 18.
- D differed from the count-normalized baseline in 6 of 17 eligible assessments and from the portal baseline in 9 of 17.

One D assessment reported encountering analyst opinions but not using them. No assessment reported using analyst opinions. The refined term audit detected no analyst-related terms in D's structured evidence, thesis, risks, or captured web actions.

These are observable disagreements with direct analyst ratings. Because v1.2.0 included forward P/E, they cannot establish complete independence from analyst-derived information, reveal private reasoning, or eliminate latent training-data influence.

## Evidence and source audit

- 532 structured evidence items were retained.
- No evidence date was invalid or later than the July 24, 2026 cutoff.
- D contained 97 cited web-evidence items: 96 primary and one secondary.
- Those citations covered 45 unique URLs across 11 domains.
- 85 of 97 citation instances exactly matched a URL captured in the API search trace.
- Four more instances matched after conservative URL normalization.
- The remaining eight instances represented two unique official issuer PDF URLs and were manually reviewed.

The last point remains a traceability warning. Before a full run, citations should pass an automatic exact-or-normalized source-trace gate, with unmatched URLs flagged for review.

## Usage, cost, and scale

Recorded successful usage was:

- 724,792 input tokens;
- 106,967 output tokens;
- 831,759 total tokens; and
- 107 web-search calls.

Using the official [GPT-5.4 rates](https://developers.openai.com/api/docs/models/gpt-5.4) and [API tool pricing](https://developers.openai.com/api/docs/pricing), the known cost is **at least $4.486485**. It is a lower bound because usage from the two original truncated calls was not archived.

The State Street snapshot contained 504 securities as of July 23, 2026. Scaling the pilot average gives a rough planning estimate of about **$126 for one full-universe repetition** or **$377 for three**. These are not budget guarantees: company mix, web-search behavior, prices, rate limits, and retries can change actual cost.

## What this pilot supports

The results support three limited conclusions:

1. The model is highly sensitive to the quality of information supplied.
2. Web access changes some classifications in both directions rather than mechanically copying analysts or increasing bullishness.
3. Some sufficient-evidence classifications differ from the direct analyst-rating baselines without observable use of those ratings.

The pilot does not prove independent reasoning or show that AI recommendations outperform analysts or SPY. The former requires a corrected analyst-derived-input-free design; the latter requires timestamped, prospective predictions and a preregistered portfolio rule.

## Decision before the full run

Ask the professor to approve:

1. Yahoo/yfinance as a provisional academic feasibility source or a licensed replacement;
2. a corrected v1.3.0 six-stock rerun without forward P/E;
3. one versus three repetitions;
4. a budget with retry headroom; and
5. the prospective portfolio protocol that will compare future AI-selected returns with SPY.

`yfinance` describes itself as an unofficial research/educational tool and reminds users that Yahoo data are intended for personal use; see its [project page](https://pypi.org/project/yfinance/).
