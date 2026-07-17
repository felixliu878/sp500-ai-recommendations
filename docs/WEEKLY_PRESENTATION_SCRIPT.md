# Weekly presentation script

Target length: approximately 8 minutes. The same wording is embedded in the PowerPoint speaker notes.

## Slide 1 — When does AI add judgment?

This week I converted our idea into a controlled experiment and a working Python pipeline. The central question is not simply whether an AI says buy or sell. It is when that answer changes, what evidence caused the change, and whether the result is genuinely different from an analyst consensus. I will show the four experimental conditions, what is already working, what the Micron dry run proves, and the two inputs I need before producing real model results.

## Slide 2 — One question, four controlled information sets

For every stock, the decision target stays fixed: a five-level classification over twelve months, relative to SPY, as of one frozen date. Only the information set changes. Condition A supplies the company identity, with no dated market data and no web access. Condition B adds twenty-four monthly adjusted prices and volume. Condition C adds locally calculated return, trend, volatility, benchmark, sector, and fundamental fields, but still no web. Condition D adds current web search and captures its sources. The model never receives analyst consensus directly. If web search exposes an analyst view, the model must disclose whether it encountered and used it. This design lets us attribute a rating change to an added information condition instead of changing everything at once.

## Slide 3 — The pipeline freezes inputs before it compares outputs

The workflow has three auditable stages. First, acquisition: the code saves a dated State Street SPY holdings workbook, obtains end-of-day prices, and, when the production key is present, retrieves fundamentals and analyst consensus. Second, assessment: it calculates every feature locally, renders a versioned prompt, uses a strict JSON schema, enables web search only in Condition D, and saves the raw response. Third, comparison: it converts both AI and analyst labels to the same ordinal scale, calculates agreement and condition changes, and preserves the evidence needed for reasoning analysis. Nothing important exists only in a notebook or a chat window; every input and output is written to a run-specific folder.

## Slide 4 — The framework is ready before paid keys arrive

The no-key pilot completed all seventy-two planned records: six stocks, four conditions, and three repetitions. Nine unit tests passed, covering feature calculations, prompt isolation, schema requirements, analyst-score math, and ticker conversion. The audit snapshot contained five hundred four constituent-security holdings after removing cash and a temporary corporate-action receivable, and it is dated July sixteenth. I will not assume that the full API universe has exactly five hundred ticker rows, because multiple share classes affect the count. The manifest will record the actual number. One full repetition is roughly two thousand model calls; three are roughly six thousand. The live recommendations and analyst fields are the only intentionally incomplete pieces.

## Slide 5 — Micron dry run proves routing, not a recommendation

This Micron example is deliberately labeled dry run, not AI. The system retrieved five hundred two trading-day observations. In the prototype feed, Micron's twelve-month adjusted return was approximately six hundred thirty-one percent, about six hundred eleven percentage points above SPY, with annualized sixty-day volatility of about one hundred eight percent. Those unusually large signals are exactly why source validation matters. The fixed test rule produced Hold in A and Strong Buy in B through D, but that is not an investment conclusion. It only proves that different packets reached the correct routes, that the ordinal outputs were stored, and that Condition D did not secretly use web search in the no-key backend. Real Micron ratings begin only after the model and financial-data keys are added.

## Slide 6 — Trust requires separating four different claims

The study must distinguish four claims that are easy to blur together. First, information sensitivity: we can test whether the same model changes its rating when evidence is added. Second, agreement: consensus counts let us measure whether the AI and analysts reach the same category. Third, independence: exposure flags and retrieved sources provide evidence about whether analyst views entered the response, but self-report alone is not proof. Fourth, performance: only timestamped predictions followed by future returns can test outperformance. Aggregate analyst counts cannot tell us whether the AI used the same reasoning as individual analysts; that would require licensed report-level rationales. Likewise, a backtest created today with today's model risks look-ahead, model-version, and survivorship bias. The defensible portfolio test is prospective.

## Slide 7 — Approve the protocol; then we can generate real evidence

My request is to approve the four-condition protocol and provide the OpenAI and financial-data API keys. I will first run the live six-stock pilot, inspect citations, analyst coverage, and cost, and freeze the configuration. We then need one cost decision: one full-universe repetition, at roughly two thousand calls, or three repetitions, at roughly six thousand calls. Three supports a stability test; one is the cheaper first pass. After the full run, the next research stage is a preregistered forward AI portfolio compared with SPY. The framework is ready; the remaining step is to replace the clearly labeled validation backend with the authorized live sources.

## Likely questions and concise answers

**Is Condition A really “historical data only”?**
No. It is company identity with no supplied dated data or web tool. The model may still contain latent training knowledge, so I call it a memory-only information-ablation baseline, not a point-in-time historical model.

**Is the AI just summarizing analysts?**
The design tests that by withholding consensus from every prompt, recording whether D encounters analyst views, and comparing the output afterward. A different rating suggests non-equivalence, but true reasoning independence needs source/rationale inspection.

**Why not scrape Google Finance?**
The portal is useful for a manual cross-check, but an API with archived raw counts is more reproducible and less fragile. Google Finance's aggregate display also does not provide systematic analyst reasoning.

**Why three repetitions?**
Model responses can vary. Repetitions estimate within-condition stability, which is necessary before interpreting a difference between conditions.

**Can we backtest immediately?**
We can do an exploratory simulation, but the credible performance test is forward-looking with timestamped predictions and a frozen portfolio rule.
