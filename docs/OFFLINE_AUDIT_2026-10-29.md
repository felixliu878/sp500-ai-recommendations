# Offline audit of the full-scale run

Audit date: October 29, 2026. Source run: `official_full_2026-08-07_20260807T151128219298Z` (frozen cutoff August 7, 2026). This audit read archived state, checkpoints, raw responses, and the canonical `analysis_final_attempt_limited/` files. It made **no model or API calls** and did not modify the run, its state, or the canonical analysis. The primary dataset remains 5,956 accepted assessments with 32 documented exclusions.

The professor-facing summary is the two-page decision memo at `output/pdf/Decision_Memo_2026-10-29/Decision_Memo_2026-10-29.pdf` (local; the folder is not published by `.gitignore`).

## Reproduce

```bash
project_dir="/Users/felix/Documents/Julia's RA Work"
run_path="$project_dir/official_runs/official_full_2026-08-07_20260807T151128219298Z"

PYTHONPATH=src .venv/bin/python scripts/analyze_official_progress.py \
  --run-dir "$run_path" --output-dir "$run_path/analysis_handoff_review_2026-10-29"

PYTHONPATH=src .venv/bin/python scripts/audit_official_run.py \
  --run-dir "$run_path" --output-dir "$run_path/audit_2026-10-29" \
  --compare-dir "$run_path/analysis_handoff_review_2026-10-29" \
  --shareable-dir "$project_dir/outputs/audit_2026-10-29"
```

The audit uses 10,000 resamples and seed `20261029` by default, and it takes a few seconds. It refuses to write into `analysis_final_attempt_limited/` or `analysis_interim/`.

## 1. Reconciliation

All 20 checks in `reconciliation.json` pass.

| Check | Result |
|---|---|
| Task registry SHA-256 and plan SHA-256 | Match the frozen plan |
| Planned tasks | 5,988 unique task IDs and 5,988 unique security × condition × repetition cells; 499 securities; 1,497 per condition |
| State vs registry | Same task set; 5,956 `ACCEPTED` + 32 `FAILED` |
| Checkpoints | 6,286 attempt files, equal to the attempts recorded in state |
| Accepted tasks | Exactly one accepted checkpoint each, always the final attempt; record identity and prompt/packet hashes match the registry |
| Failed tasks | No accepted checkpoint; all at the effective three-attempt ceiling |
| `accepted_assessments.csv` | 5,956 unique task IDs equal to the accepted state set; identity matches the registry |
| D packets | All 1,497 D packets are hash-identical to the matching C packets |
| Regenerated analysis | All 10 files in `analysis_handoff_review_2026-10-29/` are byte-identical to `analysis_final_attempt_limited/` |

**Validator replay.** The current `validate_assessment` is byte-identical to the frozen runtime copy. Re-running ingestion's identity check and validator on every archived attempt reproduced 5,956 of 5,956 acceptances and 246 of 246 recorded rejections. Another 84 attempts had no output to validate: 74 had no output text (quota or Batch errors) and 10 had unparseable, truncated JSON.

Acceptance by attempt: 5,772 on the first attempt, 134 on the second, and 50 on the third. Retries were concentrated in D, which had 172 accepted retries.

## 2. Security-clustered uncertainty

Each statistic is a ratio of sums over units: pairs, securities, or assessments. Intervals are 95% percentiles from 10,000 resamples that draw whole securities with replacement, one shared resample matrix for all statistics. Paired differences therefore use the same resampled securities on both sides. The design effect is (cluster SE / naive iid SE)².

| Statistic | Estimate | 95% interval | Units | Design effect |
|---|---:|---:|---:|---:|
| Rating changed, A→B | 8.35% (125/1,497) | 6.41–10.42% | 1,497 pairs | 2.05 |
| Rating changed, B→C | 56.05% (839/1,497) | 52.51–59.52% | 1,497 pairs | 1.90 |
| Rating changed, C→D | 39.86% (584/1,465) | 36.80–42.92% | 1,465 pairs | 1.52 |
| Rating changed, A→C (direct) | 61.12% (915/1,497) | 57.52–64.66% | 1,497 pairs | 2.08 |
| Rating changed, B→D (direct) | 42.66% (625/1,465) | 39.40–46.03% | 1,465 pairs | 1.68 |
| Rating changed, A→D (direct) | 45.46% (666/1,465) | 42.00–48.80% | 1,465 pairs | 1.78 |
| Mean ordinal change, B→C | −0.251 | −0.305 to −0.194 | 1,497 pairs | 2.41 |
| Mean ordinal change, C→D | +0.238 | +0.196 to +0.281 | 1,465 pairs | 1.72 |
| Mean ordinal change, A→D | −0.074 | −0.126 to −0.022 | 1,465 pairs | 2.33 |
| Three-run unanimity, A (matched 474) | 99.58% | 98.94–100.00% | 474 securities | — |
| Three-run unanimity, B (matched 474) | 88.82% | 85.90–91.60% | 474 securities | — |
| Three-run unanimity, C (matched 474) | 68.78% (326) | 64.61–72.82% | 474 securities | — |
| Three-run unanimity, D (matched 474) | 54.85% (260) | 50.42–59.20% | 474 securities | — |
| **D − C unanimity** | **−13.92 pts** | **−19.92 to −8.00** | 474 securities | — |
| Count-normalized exact match, C | 22.34% | 19.12–25.75% | 1,459 pairs | 2.36 |
| Count-normalized exact match, D | 29.61% | 26.34–32.97% | 1,459 pairs | 2.08 |
| **D − C, count-normalized** | **+7.27 pts** | **+4.42 to +10.21** | 1,459 pairs | — |
| Yahoo portal exact match, C | 18.49% | 15.41–21.68% | 1,417 pairs | 2.47 |
| Yahoo portal exact match, D | 21.67% | 18.58–24.90% | 1,417 pairs | 2.23 |
| **D − C, Yahoo portal** | **+3.18 pts** | **+0.63 to +5.75** | 1,417 pairs | — |
| D reported encountering analyst opinions | 17.95% (263/1,465) | 15.75–20.25% | 1,465 assessments | 1.28 |

Design effects are about 1 for unanimity because each security contributes one unit. For pair-level statistics they range from 1.5 to 2.5, so naive intervals would be roughly 1.2 to 1.6 times too narrow. No bootstrap resample reversed the sign of the D − C unanimity difference or of the count-normalized difference. For the portal difference, 0.8% of resamples did.

Ratings also move in a consistent direction. B→C has 607 downgrades against 232 upgrades, while C→D has 457 upgrades against 127 downgrades. Web access moved ratings back toward Hold and Buy after the quantitative packet had pushed many toward Sell.

These intervals describe sampling variation across securities only. They do not cover model-version, prompt, or data-source uncertainty, and they do not model correlation between securities in the same sector.

## 3. Missingness

All 32 exclusions are D tasks across 25 securities. By repetition there are 14 in r1, 10 in r2, and 8 in r3.

**By sector** (`missingness_by_sector.csv`; hypergeometric upper tail, Bonferroni across 11 sectors):

| Sector | Securities | Affected | Expected if random | p (≥ observed) | Bonferroni p |
|---|---:|---:|---:|---:|---:|
| Consumer Staples | 33 | 6 | 1.65 | 0.0038 | 0.041 |
| Information Technology | 83 | 7 | 4.16 | 0.103 | 1.00 |
| Consumer Discretionary, Health Care, Industrials | 54 / 59 / 74 | 3 each | 2.7–3.7 | ≥ 0.52 | 1.00 |
| Real Estate / Utilities | 31 / 31 | 2 / 1 | 1.55 | ≥ 0.47 | 1.00 |
| Communication Services, Energy, Financials, Materials | — | 0 | — | 1.00 | 1.00 |

Sector labels are the ones recorded in the frozen task registry.

**By security characteristics** (`missingness_security_profile.csv`; two-sided permutation test, 10,000 permutations at the security level):

| Metric | Affected (25) | Other (474) | Difference | p |
|---|---:|---:|---:|---:|
| Mean C rating score | −0.547 | −0.306 | −0.241 | 0.084 |
| C three-run unanimity | 0.560 | 0.688 | −0.128 | 0.19 |
| Mean C confidence | 65.8 | 66.4 | −0.5 | 0.53 |
| Mean accepted D rating score | −0.333 (24 securities) | −0.070 | −0.264 | 0.034 |
| Count-normalized analyst score | 0.556 | 0.652 (472) | −0.095 | 0.21 |

The exclusions are therefore not missing completely at random. They concentrate among firms that had just issued forward guidance (Section 4), and those firms' accepted D ratings lean bearish. The sensitivity analysis in Section 5 shows that the headline statistics are nonetheless insensitive to including them.

## 4. Semantic audit of the 32 exclusions

`failure_evidence_audit.csv` (local only) lists every offending evidence item in every attempt of the 32 tasks: 105 items in all. The script assigns rule-based classes from the date, origin, and URL fields and the claim text, and all 105 claims were also read manually.

| Class (all attempts) | Items |
|---|---:|
| Forecast or guidance period end: observation date after the cutoff, publication on or before it, forward-guidance language | 89 |
| Pre-announced event or policy date: merger vote, product launch, rate change effective date, CMS final rule | 7 |
| Rule flagged for manual review | 4 |
| Source URL on a non-web origin | 5 |
| **Publication date after the cutoff** | **0** |

The four flagged items were read manually. Three are forward-looking management statements about fiscal 2027, such as "fiscal 2027 should benefit from…" and "the fiscal 2027 operating environment would remain challenging". One is a utility's commitment to keep rates flat in 2026. None describes an observation made after the cutoff.

**Mechanism.** The frozen v1.4.0 prompt said: *"Use observation_date only when the cited claim measures a different dated period."* Forward guidance measures a different, future period, so following the instruction literally yields a post-cutoff `observation_date`. The validator rejects any evidence date after the cutoff, so the instruction and the validator conflict for guidance items. The current v1.4.1 template adds: *"Never set observation_date after the as-of cutoff: a future forecast endpoint is not an observation date and belongs only in the claim text."* That resolves the conflict for future runs without changing the archived experiment.

Grouping the 30 date exclusions by the item that caused the final rejection:

- 20 tasks cite next-fiscal-year guidance from 13 companies. Their fiscal years end in March–June, and they had reported annual results between May 11 and August 6, 2026.
- 3 tasks cite next-quarter guidance for the September 2026 quarter.
- 4 tasks cite current calendar-year (2026) guidance.
- 3 tasks cite pre-announced event or regulatory dates: an August 12 merger vote, a 2027 Medicare Advantage rate announcement, and a utility rate change effective October 1.

The two `INVALID_SOURCE_URL_ORIGIN` exclusions both come from derived valuation claims, such as a supplied price divided by company EPS or FFO guidance, or an implied dividend yield. The model labeled these `OTHER` and attached the guidance page's URL. The schema has no origin for claims that combine supplied and web data, so this is a labeling conflict. It is not evidence that the source was nonexistent or unreliable.

**Persistence.** 31 of the 32 tasks failed with the same rejection type on every attempt that returned structured output. The exception failed on a date on attempt 1, returned no output on attempt 2, and failed the URL-origin rule on attempt 3. More paid retries under the frozen rule would most likely fail the same way.

**Accepted D results use guidance too** (`accepted_d_guidance_dating.json`). 629 of 1,465 accepted D results (42.9%) cite at least one web `MANAGEMENT_GUIDANCE` item, 683 items in total, and this count excludes guidance filed under other categories. Of those 683 items:

| How the model set `observation_date` | Items |
|---|---:|
| Equal to the publication date | 472 |
| Null | 103 |
| Before the publication date, such as the reported period end | 80 |
| After the publication date but not after the cutoff | 28 |

Within the 25 affected securities, 24 of their 43 accepted D results cite guidance. Exclusion therefore depended on how the model dated a forecast endpoint, not on whether forward guidance was used.

**Verification limits.**

- Publication dates are model-reported. 89 of the 105 cited URLs appear among the URLs the web tool logged in that response, as search sources or opened pages. The other 16 do not appear, but the tool's source lists may be incomplete, so this neither confirms nor refutes those citations.
- Page contents were not re-fetched. Today's web would not reproduce August's pages.
- The D searches ran between August 7 and August 14, 2026, after the cutoff, so date checks rather than a point-in-time archive guard against later information.

## 5. Sensitivity analysis: alternative date rule

**Labeled sensitivity only.** The rule is not adopted, and none of the rows it adds are accepted results.

The alternative rule applies only to a web item whose publication date is on or before the cutoff and whose observation date is after it. For such an item, the observation date is cleared, as v1.4.1 instructs; every other check is unchanged, including identity, URL-origin, and post-cutoff publication checks. Archived responses are then re-validated attempt by attempt, and the first passing attempt is taken, mirroring primary acceptance. No new responses are generated.

| Statistic | Primary (frozen rule) | Fill excluded tasks only | Re-select all D attempts |
|---|---:|---:|---:|
| D rows added / replaced | 0 / 0 | 31 / 0 | 31 / 113 |
| D results | 1,465 | 1,496 | 1,496 |
| Securities with three D results | 474 | 498 | 498 |
| Rating changed, C→D | 39.86% | 40.04% | 40.44% |
| Rating changed, A→D | 45.46% | 45.19% | 44.85% |
| Three-run unanimity, C / D (complete-D cohort) | 68.78% / 54.85% | 68.27% / 54.22% | 68.27% / 54.22% |
| Count-normalized exact match, C / D (matched pairs) | 22.34% / 29.61% (1,459) | 22.21% / 29.53% (1,490) | 22.21% / 29.19% (1,490) |
| Yahoo portal exact match, C / D (matched pairs) | 18.49% / 21.67% (1,417) | 18.34% / 21.52% (1,445) | 18.34% / 21.38% (1,445) |
| D mean rating score / Hold share | −0.076 / 54.27% | −0.080 / 54.55% | −0.082 / 54.88% |
| D reported analyst exposure | 17.95% | 17.91% | 18.25% |

The largest change in any headline percentage is 0.64 points. Under the alternative rule, 31 of the 32 excluded tasks pass on an existing response, yielding 21 Hold, 9 Sell, and 1 Buy. One URL-origin task remains excluded because the date rule does not address it. The re-selection variant shows that the same date conflict also forced retries for 113 of the 172 D results that were accepted only after a retry.

## 6. Limitations of this audit

- The audit establishes data integrity, sampling uncertainty, and the mechanism behind the exclusions. It does not establish predictive accuracy, independent reasoning, or market outperformance.
- Bootstrap intervals are percentile intervals with security-level clustering only.
- Sector and characteristic tests are exploratory. Their p-values are descriptive, and the sector test is Bonferroni-adjusted.
- Failure classes are keyword rules over model-written claims, checked by manual reading. Grouping the 30 date exclusions into fiscal-year, quarter, calendar-year, and event categories was done manually from the evidence table.
- Adopting the alternative date rule for any reported result requires the professor's approval, and the primary dataset must stay as frozen.

## 7. Outputs

Local, in the run directory under `audit_2026-10-29/`:

| File | Content |
|---|---|
| `reconciliation.json` | Every check, the replay counts, and the byte-comparison results |
| `condition_pair_transitions.csv` | Adjacent and direct changes, including A→D, with upgrades and downgrades |
| `bootstrap_intervals.csv` | All levels and paired differences, with SEs and design effects |
| `missingness_by_sector.csv`, `missingness_security_profile.csv` | Missingness tests |
| `failure_task_audit.csv` | One row per excluded task: attempt outcomes, classes, and alternative-rule result |
| `failure_evidence_audit.csv` | One row per offending evidence item, including model-written claims and URLs |
| `failure_audit_summary.json`, `accepted_d_guidance_dating.json` | Aggregate failure and guidance-dating counts |
| `sensitivity_alternative_date_rule.csv` | Primary and both sensitivity variants |
| `audit_summary.json` | Headline intervals and guardrails |

Shareable aggregate-only copies are in `outputs/audit_2026-10-29/`. They exclude the per-task and per-item failure files.
