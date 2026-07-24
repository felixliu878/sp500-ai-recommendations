# Weekly presentation script

Target length: approximately three minutes.

## Slide 1 — Research question

This week I completed the live pilot for our AI stock-recommendation study. The question is not only whether AI says Buy or Hold. We want to know when its answer changes as we add information, how it compares with analysts, and what could count as additional AI judgment.

## Slide 2 — Four controlled environments

I held the model, date, twelve-month SPY-relative target, and rating scale fixed. Only the information changed. A received company identity; B added price history; C added technical, benchmark, sector, and fundamental data; and D added web search to the same C packet. Analyst ratings were withheld and joined only afterward.

## Slide 3 — The system I built

The Python system freezes the data, calculates features, builds the four prompts, calls the API, and archives completed responses and usage. Failed calls keep auditable error records. It blocks paid calls when data quality fails. Thirty-nine automated tests plus thirty-two subtests pass.

## Slide 4 — Live pilot result

The pilot covered six stocks, four conditions, and three repetitions: seventy-two final cells. Two web responses were truncated, so I retried only those exact prompts; both succeeded, with the failures preserved.

A produced eighteen Holds, B produced seventeen Holds and one Buy, C produced thirteen Buys, and D produced twelve. Confidence rose from twenty-seven in A to sixty-six in C. Quantitative data caused the biggest shift.

## Slide 5 — Web search and analyst comparison

Web search did not simply make the model bullish. From C to D, nine of eighteen ratings changed: five Buy to Hold and four Hold to Buy.

All six count-based analyst benchmarks were Buy. AI matched them in seventy-two percent of C results and sixty-seven percent of D results. Under the recorded sufficient-evidence screen, C still disagreed five times and D six times. No response reported using analyst ratings.

## Slide 6 — What the pilot can show

The pilot shows information sensitivity and disagreement with direct analyst ratings, but it cannot prove private reasoning. In a final audit, I found that C and D included forward P/E, which can reflect analyst earnings forecasts. So the strict independence claim would be too strong. I removed that field in version 1.3, while keeping the completed version 1.2 pilot unchanged.

## Slide 7 — Next decision

Recorded cost is at least four dollars and forty-nine cents. One repetition over the current 504-security snapshot is roughly 126 dollars. I also fixed full-universe sector coverage and made the quality gate mandatory for every paid run. The next step is a corrected six-stock version 1.3 pilot. Only after that and budget approval should we scale and later compare a timestamped AI portfolio with SPY.

## Short answers for likely questions

**Is AI just summarizing analysts?**

The direct rating baselines are withheld and compared only afterward. However, version 1.2 included forward P/E, an analyst-derived input, so I describe its independence result as exploratory. Version 1.3 removes it.

**Did AI outperform the market?**

That has not been tested yet. This pilot studies behavior and information sensitivity. Outperformance requires future returns.

**Why use retries?**

The two failures were truncated JSON, not rejected stock assessments. The retry process reused the exact prompts, preserved the failures, and labeled the two replacement rows.

**Why not run all 504 securities now?**

The pilot establishes quality and cost first. A corrected version 1.3 pilot, source-use approval, and budget approval come before the full run.
