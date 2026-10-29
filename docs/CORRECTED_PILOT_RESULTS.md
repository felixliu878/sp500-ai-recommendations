# Corrected Live Pilot Results

## Status

The July 31, 2026 pilot is complete. Six stocks, four information conditions, and three repetitions produced 72 accepted assessments. One original Condition D response used an evidence date after the cutoff and was rejected; an append-only retry reused the exact archived prompt and succeeded. The consolidated dataset passed every hard validation check. Six automatically unmatched primary-source URLs were then manually verified as dated on or before the cutoff and supportive of the cited claims.

## Results

| Condition | BUY | HOLD | Mean confidence |
|---|---:|---:|---:|
| A - identity only | 0 | 18 | 26.39 |
| B - price history | 0 | 18 | 39.22 |
| C - quantitative | 14 | 4 | 65.22 |
| D - current web | 12 | 6 | 63.22 |

Moving from B to C changed 14 of 18 matched ratings, all from HOLD to BUY. Moving from C to D changed 8 of 18: five BUY-to-HOLD and three HOLD-to-BUY. Web access therefore did not simply make the model more bullish.

The count-normalized analyst benchmark was BUY for all six stocks. AI exact-match rates were 0% in A, 0% in B, 77.78% in C, and 66.67% in D. This is observable evidence that the model did not merely repeat the benchmark, but the two systems do not have identical targets: the AI predicts SPY-relative performance while portal ratings are generally absolute recommendations.

Across the 24 stock-condition groups, 18 were unanimous across the three repetitions. Mean modal agreement was 91.67%, up from 88.89% in the earlier pilot. The corrected pattern is otherwise very similar to the earlier run, but changes cannot be attributed solely to removing forward P/E because the market-data cutoff also advanced by one week.

## Analyst-exposure interpretation

A-C had no web calls, no supplied analyst ratings, and no detected analyst exposure. One of 18 D assessments encountered MarketBeat during search, disclosed the encounter, and reported that the analyst opinion was not used. A separate text-audit flag on AAPL was a false positive caused by the phrase "upgrade-cycle catalyst," not an analyst upgrade.

The defensible claim is therefore behavioral: a rating produced under analyst-withheld conditions, supported by non-analyst evidence, and differing from or changing independently of the analyst benchmark is consistent with the model applying its own judgment. The experiment cannot directly observe or prove the model's hidden reasoning.

## Usage and cost

Successful input-token averages were 982 for A, 1,975 for B, 3,352 for C, and 38,720 for D. D is larger because retrieved web content is billed as input. The 72 accepted calls cost $4.95344. Including the fully recorded rejected call and exact retry, total paid-attempt cost was $5.16333 at $2.50 per million input tokens, $15 per million output tokens, and $0.01 per web-search call.

The official current snapshot contains 503 security rows after excluding a cash row. The full data gate found 499 model-ready securities. For those 499 securities, four conditions, and three repetitions, the pilot profile implies about $412 before retries or about $429 if the observed retry rate repeats. This is an estimate rather than a spending guarantee.

## Scale-up gate

The full-universe dry audit is complete. The official snapshot contains 503 securities; 499 are model-ready and passed a 1,996-assessment dry run. HONA, Q, and FDXF have fewer than 253 price observations, and PSKY is below the minimum fundamental-coverage threshold. Among the 499 eligible securities, the count-distribution analyst baseline is available for 497 and the portal baseline for 485.

The paid 5,988-assessment run should start only after execution is checkpointed/resumable, because the observed pilot pace implies roughly 34 hours sequentially. Missing analyst baselines should be treated as comparison missingness rather than model-data failure.
