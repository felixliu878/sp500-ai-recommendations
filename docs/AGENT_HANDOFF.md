# Project handoff: S&P 500 AI recommendation research

Prepared October 29, 2026, for an incoming agent continuing Julia Yang's research at Emory University's Goizueta Business School.

## 1. Start here

The project has progressed beyond the pilot and engineering stage. A full-scale experiment was executed across **499 securities, four information conditions, and three independent repetitions**. It accepted **5,956 of 5,988 planned assessments (99.47%)**. The remaining **32 Condition D assessments** exhausted the approved three-attempt limit and remain excluded.

The next useful work is **offline analysis and research-design finalization**, not an automatic rerun. The study has established information sensitivity and measured repeatability and analyst agreement. It has **not established predictive accuracy, independent internal reasoning, or market outperformance**. A portfolio protocol and prospective performance evaluation are still pending.

Read this document, then [FULL_SCALE_RESULTS.md](FULL_SCALE_RESULTS.md), [METHODOLOGY.md](METHODOLOGY.md), and [OFFICIAL_FULL_RUN_FRAMEWORK.md](OFFICIAL_FULL_RUN_FRAMEWORK.md). Prefer the frozen full-run records over older pilot reports when resolving discrepancies.

### Workspace and repository

- Local project root: `/Users/felix/Documents/Julia's RA Work`
- GitHub: <https://github.com/felixliu878/sp500-ai-recommendations>
- Branch: `main`
- Last verified synchronized commit before this handoff: `6d2409bb26e1b8c6a7b894a9663e57c139875195`
- That commit contains the full-run framework, recovery scripts, updated documentation, aggregate findings, and the latest PDF/LaTeX report.
- The main worktree was clean when handoff preparation began. This handoff is a new local document; do not assume it has already been committed or pushed.

Paths below are relative to the project root unless explicitly absolute. A GitHub-only agent will need additional local artifacts to perform row-level analysis.

## 2. Research question and controlled experiment

The professor's original question was whether AI stock recommendations merely resemble existing analyst opinions or change in response to additional evidence. The experimental design varies the model's permitted information while holding the decision task and model settings constant.

| Condition | Information available | Accepted / planned |
|---|---|---:|
| A: `A_MEMORY_ONLY` | Company name, ticker, sector, cutoff, and common decision task | 1,497 / 1,497 |
| B: `B_PRICE_HISTORY` | A plus 12 monthly adjusted-close and volume observations | 1,497 / 1,497 |
| C: `C_QUANTITATIVE` | B plus technical indicators, fundamentals, SPY, and sector context | 1,497 / 1,497 |
| D: `D_CURRENT_WEB` | The identical C packet plus web search | 1,465 / 1,497 |

Important design details:

- One experimental unit is `security × condition × repetition`. The three repetitions are independent model assessments, **not API retry attempts**.
- The archived model snapshot is `gpt-5.4-2026-03-05`, with low reasoning effort and a five-category rating scale.
- The task asks for expected **12-month risk-adjusted performance relative to SPY**. This is an evaluation instruction, not a future return or target price supplied to the model.
- Ordinal encoding is `STRONG_BUY=2`, `BUY=1`, `HOLD=0`, `SELL=-1`, `STRONG_SELL=-2`. The encoding orders categories; it does not imply equal economic return differences.
- The universe was derived from a dated State Street SPY holdings snapshot. There were 503 security rows after cash exclusion; 499 passed the model-input gate. The historical exclusions were HONA, Q, FDXF, and PSKY. Securities and share classes are not necessarily 500 distinct companies.
- Yahoo Finance interfaces and `yfinance` supplied prices, fundamentals, and analyst snapshots. This is a provisional unofficial data source, not a licensed historical point-in-time feed.
- Monthly prices are the **last included trading-day adjusted close** in each calendar month, not monthly averages. Volume is summed through the actual observation date; the latest month may be partial. Daily histories support technical-feature calculation.
- Forward P/E and direct analyst ratings were excluded from supplied full-run model packets. D may nevertheless encounter analyst material through search, so exposures and search actions are audited.

### Dates and versions: keep these distinct

| Artifact | Protocol and status |
|---|---|
| July 24 pilot | Historical `v1.2.0`; included forward P/E; 72 accepted cells after 74 attempts |
| July 31 corrected pilot | `v1.3.0`; forward P/E removed; 72 accepted cells after 73 attempts; up to 24 monthly price observations |
| August full run | Frozen `v1.4.0`; **actual data cutoff August 7, 2026**; 12 monthly observations; 5,956 accepted cells |
| Current working templates | `v1.4.1`; compact prompts, clearer evidence-date/origin instructions, and a four-call web-tool cap for future requests |
| Latest progress report | Reporting cutoff October 28, 2026; summarizes the archived experiment |

The current working configuration still contains the historical August date. It is **not ready for a fresh same-day acquisition without deliberate date/configuration changes**. Do not change archived cutoff metadata to make old results appear current. The user requested removal of the experiment-cutoff line from the report's opening; that layout edit did not change the underlying experiment or forecast horizon.

## 3. Authoritative artifacts and transfer requirements

### Primary local run

```text
/Users/felix/Documents/Julia's RA Work/official_runs/official_full_2026-08-07_20260807T151128219298Z
```

Run ID: `official_full_2026-08-07_20260807T151128219298Z`.

Frozen plan SHA-256: `51411402cf97ececc2e6fafcfcd834b8ba73578148e2920e2b529438f8953c2b`.

| File or directory inside the run | Purpose |
|---|---|
| `analysis_final_attempt_limited/` | Canonical attempt-limited analysis; prefer over `analysis_interim/` |
| `analysis_final_attempt_limited/accepted_assessments.csv` | 5,956 accepted row-level assessments, including explanations, evidence, and analyst comparisons |
| `analysis_final_attempt_limited/failure_summary.csv` | 32 excluded tasks, their attempts, causes, and final errors |
| `state.json` | Per-task and per-shard execution state |
| `official_plan.json` | Original frozen execution plan |
| `frozen_inputs/task_registry.jsonl` | All 5,988 planned task identities and their frozen requests |
| `frozen_inputs/experiment_config.json` | Settings that actually generated the archived requests |
| `frozen_inputs/eligible_analyst_consensus.csv` | Frozen external analyst benchmarks joined after model assessment |
| `frozen_inputs/runtime_source/` | Archived runtime; use when auditing original execution |
| `checkpoints/` | Accepted/failed attempts and provenance; accepted state points to the selected checkpoint |
| `raw_responses/`, `downloads/`, `batch_inputs/`, `prompts/` | Raw model outputs, downloaded batch files, exact requests, and prompts |
| `FULL_RUN_APPROVAL.json`, `RECOVERY_PLAN.json`, `RECOVERY_APPROVAL.json` | Original approval and separately authorized recovery provenance |
| `progress/index.html`, `progress/progress.json` | Local execution dashboard and its saved snapshot |

The latest read-only status check on October 29 returned `PERMANENT_FAILURE`, `APPROVED`, 5,956 accepted tasks, 32 failed tasks, 23 ingested shards, and 4/4 smoke tasks accepted. Here `PERMANENT_FAILURE` means the approved attempt limit was exhausted. It does **not** mean the whole dataset is unusable. `analysis_status` is correctly `PARTIAL`, not `COMPLETE`.

There are 6,212 downloaded response records counted in the archived cost analysis, including retries. Do not treat that as 6,212 unique accepted experimental units. Select one accepted checkpoint per unique `task_id`.

### What GitHub does and does not contain

GitHub contains code, tests, current documentation, `outputs/official_summary/` aggregate JSON/CSV files, and the dated PDF/LaTeX report. It intentionally excludes the full official run, provider caches, credentials, detailed raw responses, and the simplified per-stock workbook.

For an agent on another machine, transfer the **entire official run directory intact through a private channel**. Include provider caches under `data/raw/` if source-acquisition auditing is needed. For presentation work, also transfer the workbook and, if needed, the separately managed dashboard project described below. Do not include credentials in an ordinary handoff package. A clone alone cannot reproduce the row-level findings or reconstruct historical Yahoo snapshots; it can inspect aggregates and run the mocked tests.

### Professor-facing deliverables

- PDF: `output/pdf/Research_Progress_2026-10-28/Research_Progress_Report_2026-10-28.pdf`
- Editable source: `output/pdf/Research_Progress_2026-10-28/main.tex`
- Simplified workbook, local-only: `outputs/professor_table_2026-09-05/AI_Stock_Experiment_Professor_Table.xlsx`
- Workbook authoring script, local-only: `tmp/professor_table_2026-09-05/build_professor_table.mjs`
- Shareable aggregate findings: `outputs/official_summary/`

The workbook's `Results` sheet retains **all 5,988 planned rows**. Columns are company, ticker, test number, condition, AI rating, confidence, count-normalized analyst rating, Yahoo portal rating, exact matches to each benchmark, task status, and failure reason. Failed rows have blank AI results and an explicit cause. Its `Notes` sheet defines these terms. The professor requested underlying rows to investigate recommendation changes and verify summary calculations, not just a smaller summary table.

## 4. Verified findings and interpretation

### Information sensitivity

| Comparison | Matched assessments | Rating changed |
|---|---:|---:|
| A to B | 1,497 | 8.35% |
| B to C | 1,497 | 56.05% |
| C to D | 1,465 | 39.86% |
| Direct A to D | 1,465 | **45.46% (666/1,465)** |

**Do not add adjacent percentages.** A prior interpretation suggested about 95%; the correct direct A-to-D change rate is 45.46%. A rating can change multiple times or return to its initial category.

### Repeated-run stability

For the **same 474 securities with all three D results accepted**, exact unanimity across three ratings was **326/474 (68.78%) in C** and **260/474 (54.85%) in D**. The unmatched C-only cohort of 499 securities instead gives 68.14%; do not silently mix the two denominators.

The 54.85% figure is not prediction accuracy. It means about 45% of that complete D cohort had at least one differing rating. Mean modal agreement is a different statistic and must not be labeled exact three-run stability. A's high stability also needs qualification: all A assessments flagged insufficient evidence, and 99.73% selected Hold.

### Analyst comparisons

| Separate analyst baseline | Matched C-D pairs | C exact match | D exact match |
|---|---:|---:|---:|
| Count-normalized recommendation | 1,459 | 22.34% | 29.61% |
| Yahoo portal recommendation | 1,417 | 18.49% | 21.67% |

The first baseline is the researcher-calculated weighted score from analyst counts, mapped to the five categories. The second is Yahoo's displayed overall recommendation. `src/sp500_ai/analysis.py` is authoritative for the score and mapping thresholds. These baselines can differ and must remain separate. The frozen August gate has count-baseline coverage for 497 securities and portal coverage for 482; older July documents mention 485 portal ratings.

Analyst agreement measures similarity, not correctness. Analyst rating conventions also do not exactly match the AI's SPY-relative task. D reported encountering analyst opinions in **263/1,465 assessments (17.95%)**, while none reported using them. Self-reports and search-term audits cannot establish internal independence. There is no report-level dataset comparing the detailed reasoning of individual analysts.

Self-reported confidence is not a calibrated probability of correctness. No paper acceptance, conference presentation, or completed portfolio outperformance result is established by these artifacts.

### Failure causes and cost

All 32 unresolved tasks are in D, across 25 securities, after three approved attempts:

- **30 `FUTURE_OBSERVATION_DATE`**: an evidence observation date failed the frozen cutoff check. Some refer to forecast-period endpoints. Review the original claim before calling this genuine future-information leakage.
- **Two `INVALID_SOURCE_URL_ORIGIN`**: a URL was assigned to an evidence origin that does not permit URLs. This does not by itself establish that the website was nonexistent or unreliable.

Earlier quota and truncation errors were recovery-stage issues. They are not the final causes of these 32 exclusions. Do not convert failed tasks to Hold, omit them silently, or attribute them all to insufficient credits.

The archived usage estimate is **$243.241438 (about $243.24), a lower bound**, not a reconciled invoice. D contributes about 83.5%. Pricing assumptions are frozen in `cost_summary.json`; they are not current-price guarantees. Current API credit balance was not checked for this handoff. The old 34-hour estimate assumed sequential pilot calls; actual full-run execution used parallel asynchronous batches. A 24-hour completion window is not an exact ETA.

## 5. Engineering map and safe local commands

| Component | Main file |
|---|---|
| Provider adapters and data acquisition | `src/sp500_ai/providers.py`, `src/sp500_ai/pipeline.py` |
| Technical and monthly price features | `src/sp500_ai/features.py` |
| Prompt construction and condition templates | `src/sp500_ai/prompts.py`, `prompts/` |
| Response schema and evidence validation | `src/sp500_ai/llm.py` |
| Batch plans, hashes, transfers, checkpoints, retries | `src/sp500_ai/official.py` |
| Progress/ETA snapshot and HTML renderer | `src/sp500_ai/progress.py` |
| Ordinal ratings and analyst joins | `src/sp500_ai/analysis.py` |
| Official runner CLI | `scripts/official_full_run.py` |
| Full-run summaries and failure/cost accounting | `scripts/analyze_official_progress.py` |
| Frozen-runtime ingestion parser correction | `scripts/resume_official_full_run_hotfix.py` |
| Separately approved recovery plan | `scripts/recover_official_full_run.py` |

The ingestion fix prevents a task hash beginning with attempt-like characters from being misread as a retry suffix. The current source already contains the corrected parser; wrappers apply it when loading the older frozen runtime. Do not modify the frozen runtime or remove hash checks to make an audit pass.

### Initial checks: no paid calls

```bash
project_dir="/Users/felix/Documents/Julia's RA Work"
cd "$project_dir"
run_path="$project_dir/official_runs/official_full_2026-08-07_20260807T151128219298Z"

git status --short --branch
git log -3 --oneline
PYTHONPATH=src .venv/bin/python scripts/official_full_run.py status --run-dir "$run_path"
```

Last test verification, October 28: **52 tests and 39 subtests passed**. The tests mock transports and do not require live model calls. Rerun after relevant code changes:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

On a new machine, recreate the virtual environment using `pyproject.toml`/`requirements.txt` and the README setup. Existing `.venv` paths and installed libraries should not be assumed portable.

### Regenerate analysis offline into a new directory

The following reads archived checkpoints/downloads and writes derived files; it does not contact the model provider. Use an unused output directory so the canonical attempt-limited analysis is preserved:

```bash
PYTHONPATH=src .venv/bin/python scripts/analyze_official_progress.py \
  --run-dir "$run_path" \
  --output-dir "$run_path/analysis_handoff_review_2026-10-29"
```

The default output directory is `analysis_interim`; always provide `--output-dir` for a review. This script reports adjacent transitions but does not currently export the direct A-D count as its own table. Calculate direct A-D by joining the same `symbol` and `repetition`, with one accepted row per condition.

CSV evidence/thesis fields contain JSON strings. Parse them explicitly. Parse Boolean strings explicitly too: Python's `bool("False")` is true. Preserve missing benchmark values rather than treating them as disagreements. Resample at security level for uncertainty estimates because repeated assessments within a security are related observations.

### PDF editing

Edit `output/pdf/Research_Progress_2026-10-28/main.tex` in place and compile to PDF. The project has been checked with the desktop LaTeX compiler and local `pdflatex`. Render pages using `pdftoppm` and inspect them after layout changes. Keep the latest PDF at the existing descriptive filename, with build logs/intermediates under `tmp/pdfs/`.

The current report is two pages. It begins with an accomplishments summary, retains October 28 as the progress reporting cutoff, and omits the experiment-cutoff line at the user's request. It still records the actual source run in its evidence notes. Do not silently shift its results or the August 2027 forecast horizon.

### Dashboards

The run's `progress/index.html` is a saved local execution dashboard. It does not prove a supervisor is currently running. The `progress` CLI command rewrites local dashboard files; it is not a read-only status command.

`presentation_summary_site/` is a **separate nested Git project**, excluded from the parent repository. It is a React/vinext professor-facing presentation site, with static findings in `app/page.tsx`. Its default README is mostly starter boilerplate. It can be started locally from its own directory with `npm run dev` when a suitable Node runtime/dependencies are present; check the actual emitted URL rather than assuming a port. No live server was verified during this handoff.

Before presenting that older page, reconcile its dates and phrasing with the current report. In particular, its statement that the results show additional internal judgment is stronger than the defensible conclusion of observable information sensitivity. Do not accidentally add the nested repository or its dependencies to the parent project.

## 6. Recommended continuation plan

These are pending tasks, not completed findings or instructions to start paid work automatically.

**October 29 status:** items 1–3 were completed offline and item 4 was drafted. See Section 9.

1. **Reconcile and document the primary dataset.** Confirm 5,988 unique planned cells, 5,956 accepted cells, 32 excluded cells, and one selected accepted record per task. Compare fresh offline summaries with `analysis_final_attempt_limited/`; preserve any differences as audit findings.
2. **Add uncertainty and missingness analyses.** Compute security-clustered or security-bootstrap intervals for rating-change, agreement, and exact-stability statistics. Compare the same complete cohorts across conditions; examine whether failures concentrate by sector or company characteristics. Label sensitivity analyses separately from the primary results.
3. **Audit the 32 rejected outputs semantically.** Distinguish forecast endpoints from genuine post-cutoff observations and inspect URL-origin assignments. Do not relax the validator or relabel rejected responses as accepted. If an alternative rule is justified, preserve the primary dataset and propose a separately labeled sensitivity analysis.
4. **Prepare a short decision memo for the professor.** Confirm whether 99.47% coverage with documented exclusions is sufficient; whether analyst comparisons remain descriptive or warrant a matched absolute-return AI condition; and whether any further paid retry or new experiment is wanted. Clarify the final academic deliverable: paper, report, presentation, or another format.
5. **Freeze the prospective portfolio protocol.** Specify condition, combination of three ratings/tie handling, selection threshold, weighting, missing-rating rule, transaction costs, rebalancing, start date, and SPY comparison metrics. No final portfolio specification has been approved or implemented in the verified artifacts. Do not design a rule using returns already observed and present it as a prospective August portfolio.
6. **Evaluate investment outcomes later.** The archived 12-month forecast horizon ends in August 2027. A portfolio beginning at a newly agreed prospective date has its own horizon. Track total returns, risk, drawdowns, and costs consistently with SPY; a shorter interim result cannot establish full 12-month accuracy.

A good first new deliverable is an offline audit/uncertainty report plus the professor decision memo, without spending additional API credits.

## 7. Credentials, execution boundaries, and user preferences

- One existing local credential, `OPENAI_API_KEY`, is loaded from the ignored `.env` file for authorized live work. Do not copy its value into a handoff, terminal output, report, prompt, or Git commit. Do not assume credentials or credits transfer to another machine.
- The professor approved the historical three-run design; the archived recovery separately raised the task ceiling from two to three. Existing approval files bind to that exact plan. They are not approval for unlimited retries, a fourth attempt, a changed model, new absolute-return conditions, or a newly dated full experiment.
- The user previously asked to use the existing key. Do not create replacement keys unnecessarily. No new paid experiment is required to complete the offline continuation tasks above.
- New acquisitions need an explicit current UTC cutoff and a new frozen plan. Current Yahoo snapshots cannot reconstruct historical August analyst data. Refreshing caches is not a way to reproduce the archived experiment.
- Never run `launch`, `supervise`, `advance`, recovery `execute`, or a live `--backend openai` command just to inspect progress. Those can submit paid requests. Some preflight/progress commands also write local diagnostics even though they do not spend credits.
- Ordinary communication should be in **English**, unless the user asks for Chinese. Professor-facing writing should be clear, natural, concise, and explicit about uncertainty. Do not invent supervisor approval, research achievements, publication, or investment performance.
- If a presentation deck is requested, the user prefers **Gamma** over built-in slide generation. For reports, the latest preference is **LaTeX followed by PDF**. The handoff itself is Markdown for portability and easy copying.
- Keep raw runs, provider caches, workbook inspection dumps, temporary files, and credentials out of public GitHub. Shareable aggregate summaries and the PDF/LaTeX report are already published. Check the diff and preserve unrelated user edits before future pushes.

## 8. Suggested opening instruction for the next agent

> Continue the S&P 500 AI recommendation research using `docs/AGENT_HANDOFF.md`. First verify the archived local artifacts and current Git status. Start with an offline reconciliation, uncertainty estimates, and a semantic audit of the 32 excluded D tasks, then draft a concise decision memo for the professor. Preserve the frozen experiment and primary results. Explain any missing local artifacts before drawing new conclusions. Do not start a fresh paid run, change the historical cutoff, or add portfolio performance claims as part of that initial review.

## 9. Update: October 29 offline audit

Completed without any model or API calls. The frozen run, its state, and `analysis_final_attempt_limited/` are unchanged. Full write-up: [OFFLINE_AUDIT_2026-10-29.md](OFFLINE_AUDIT_2026-10-29.md).

- **Reconciliation.** All 20 checks pass, recorded in `audit_2026-10-29/reconciliation.json` inside the run. The regenerated analysis in `analysis_handoff_review_2026-10-29/` is byte-identical to the canonical one. Replaying the unchanged validator reproduces all 5,956 acceptances and 246 rejections.
- **Uncertainty.** `scripts/audit_official_run.py` adds security-bootstrap intervals (10,000 resamples, seed 20261029). The D − C three-run unanimity difference is −13.9 points (−19.9 to −8.0). Count-normalized analyst match is +7.3 points (+4.4 to +10.2), and Yahoo portal match is +3.2 points (+0.6 to +5.8).
- **The 32 exclusions.** No offending item carries a publication date after the cutoff. Thirty are forecast-period or pre-announced-event endpoints, the product of a conflict between the v1.4.0 dating instruction and the validator; v1.4.1 already fixes it for future runs. Two are derived valuation claims labeled `OTHER` with a URL. Consumer Staples is over-represented (Bonferroni p = 0.04).
- **Sensitivity.** A labeled alternative date rule would recover 31 of the 32 from existing responses and moves every headline percentage by 0.64 points or less. It is not adopted and requires professor approval.
- **Decision memo.** `output/pdf/Decision_Memo_2026-10-29/Decision_Memo_2026-10-29.pdf`, with `main.tex` beside it, is two pages. It is local only, because `.gitignore` publishes only `Research_Progress_*` report folders. Build files are in `tmp/pdfs/decision_memo_2026-10-29/`.
- **Tests.** `tests/test_audit_official_run.py` adds 10 tests.
- **Still pending.** The professor's answers to the memo's five questions; then the portfolio protocol (item 5) and outcome evaluation (item 6).
