# Weekly Meeting Script

> Historical script from the corrected-pilot and full-run preparation stage. For current full-scale results, see [FULL_SCALE_RESULTS.md](./FULL_SCALE_RESULTS.md) and the [progress report](../output/pdf/Research_Progress_2026-10-28/Research_Progress_Report_2026-10-28.pdf).

This week, I completed the corrected live pilot and prepared the system for the full S&P 500 experiment.

The main research question is whether AI simply summarizes analyst opinions or whether its recommendation changes when we give it different information. I tested six stocks under four controlled conditions: company identity only, price history, quantitative data, and the same quantitative data plus current web search. Each condition was run three times, producing 72 accepted assessments. Analyst ratings were not included in the prompts, and I removed forward P/E because it can contain analyst forecasts.

The main result is that the information environment clearly changed the AI's output. With identity or price history alone, all 18 ratings were HOLD. After adding quantitative data, 14 changed to BUY. With web search, 12 were BUY and 6 were HOLD. Eight of 18 ratings changed between the quantitative and web conditions, including five that became less bullish. This shows that web access did not simply push the model toward more positive recommendations.

The analyst benchmark was BUY for all six stocks. AI agreement was zero in the first two conditions, 77.8 percent with quantitative data, and 66.7 percent with web search. The defensible conclusion is that the outputs show behavior consistent with additional judgment, not merely copying analyst ratings. However, this does not prove the model's hidden reasoning or that it will outperform the market.

I also validated the experiment. Agreement across repeated runs was 91.7 percent. The system caught and reran one response that used a post-cutoff citation. Total cost, including that rejected call and retry, was $5.16, and the web condition caused most of the token cost.

I also completed the full-universe dry data audit. The official holdings file contains 503 securities after removing a cash row. Of these, 499 pass every model-data check. Three newly listed or spun-off securities do not yet have 253 price observations, and PSKY does not have enough fundamental fields. A dry run of the 499 eligible stocks completed all 1,996 test assessments successfully.

The proposed paid design is therefore 499 stocks times four conditions times three runs, or 5,988 assessments. Before launching it, I will add checkpoint and resume support. The estimated cost is about $412 before retries, or $429 if the pilot retry rate repeats. I would like to confirm two rules: first, whether we should keep valid AI ratings when an analyst baseline is missing and exclude them only from that comparison; and second, whether we should add an AI target that more closely matches the analysts' absolute-rating target.
