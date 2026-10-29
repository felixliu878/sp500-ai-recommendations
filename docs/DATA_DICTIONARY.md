# Data dictionary

## Run-level files

| File | Grain | Meaning |
|---|---|---|
| `manifest.json` | One run | Frozen scope, cutoff, backend, model, symbols, conditions, repetition count, providers, key availability, completion/failure counts, token total, and interpretation limits |
| `experiment_universe.csv` | One security | Securities actually selected for assessment |
| `universe_snapshot/spy_holdings.xlsx` | One source snapshot | Unmodified dated State Street workbook |
| `universe_snapshot/spy_holdings.csv` | One holding | Parsed holdings after removing cash and temporary contra receivables |
| `universe_snapshot/metadata.json` | One snapshot | Source URL, provider date, retrieval timestamp, and security count |
| `features.csv` | One security | Locally calculated market and technical features |
| `analyst_consensus.csv` | One security | Yahoo current rating counts, researcher normalization, separate portal baseline, price targets, retrieval time, and raw consensus payload |
| `data_quality.csv` | One security | Price freshness, fundamental coverage, provider errors, and analyst-baseline availability |
| `context_data_quality.csv` | One benchmark or sector ETF | History length, cutoff compliance, and freshness for SPY and required sector ETFs |
| `acquisition_gate.json` | One run | Pre-model quality-gate status, strictness, check time, and security-level issues |
| `recommendations.jsonl` | One assessment | Canonical nested assessment plus run identifiers and audit metadata |
| `recommendations.csv` | One assessment | Flat analysis-ready representation with both analyst comparisons |
| `condition_changes.csv` | One condition pair | Pairwise flip rate and mean absolute ordinal change |
| `assessment_errors.csv` | One failed assessment | Cell identity, sanitized error, failure time, and—when the provider returned a response—response ID, model, usage, web calls, and the archived failed-response path |
| `pilot_summary.json` | One canonical run | Completion, ratings, transitions, confidence, stability, analyst comparisons, exposure audits, web usage, tokens, cost status, and provenance |
| `validation_report.json` | One accepted local run | Credential-free integrity checks, hard failures, warnings, and audit metrics |
| `outputs/LIVE_PILOT_SUMMARY.json` | One public pilot summary | Sanitized aggregate results and disclosed v1.2.0 interpretation caveat tracked in Git |
| `outputs/LIVE_PILOT_VALIDATION.json` | One public validation summary | Sanitized credential-free integrity result tracked in Git |
| `prompts/*.txt` | One assessment | Exact rendered prompt |
| `raw_responses/*.json` | One successful assessment | Complete provider response retained for audit and re-parsing |
| `raw_responses/*.error.json` | One failed assessment | Sanitized model-call error record |
| `raw_responses/*.failed_response.json` | One returned but invalid assessment | Raw provider payload retained when parsing, identity, or evidence validation fails after the API responds |

## Consolidated retry artifacts

The canonical pilot is stored in a retry subdirectory because two original cells required targeted repair. The original run remains unchanged.

| File | Grain | Meaning |
|---|---|---|
| `retry_manifest.json` | One retry operation | Original and consolidated run IDs, exact requested cells, prompt hashes, output-token limit, success/failure count, timestamps, known original/retry failed-call usage, and unknown paid-failure usage count |
| `retry_attempts.csv` | One retry attempt | Cell identity, prompt hash, retry status, response metadata, recorded usage, web-call counts, response/error artifact paths, usage-completeness flags, and original error type |
| `original_assessment_errors.csv` | One original failed cell | Preserved errors from the source run before successful repair |
| `assessment_errors.csv` | One unresolved consolidated failure | Header-only when every requested retry succeeds |
| `recommendations.csv` | One final assessment cell | Consolidated 72-row analysis table containing original successes and targeted retry successes |
| `recommendations.jsonl` | One final assessment cell | Consolidated nested records with retry provenance |
| `manifest.json` | One consolidated run | Completion totals, retry counts, source-artifact hashes, successful and failed-response usage totals, and unknown usage |
| `pilot_summary.json` | One consolidated run | Reader-ready quantitative summary generated from canonical artifacts |
| `validation_report.json` | One consolidated run | Independent Cartesian-completeness, schema, prompt/raw, retry, packet, evidence-date, web-isolation, and source-trace checks |

The canonical pilot has 72 final cells after 74 total attempts: 70 original successes, two original truncated-JSON failures, and two successful targeted retries.

## Provider caches

| Path | Meaning |
|---|---|
| `data/raw/prices/yahoo_prototype/<cutoff>/<symbol>.csv` | Parsed dated Yahoo price history |
| `data/raw/prices/yahoo_prototype/<cutoff>/<symbol>.metadata.json` | Yahoo chart metadata and retrieval status |
| `data/raw/prices/yahoo_prototype/<cutoff>/<symbol>.raw.json` | Raw Yahoo chart response |
| `data/raw/research/yahoo_unofficial/<cutoff>/<symbol>.json` | Compact fundamentals, current analyst snapshots, raw responses, schema version, metric hash, retrieval time, and errors |

Yahoo/yfinance data are unofficial current snapshots for small academic or personal-use feasibility work, not licensed production or historical point-in-time research data.

## Recommendation identity and execution fields

| Field | Type | Definition |
|---|---|---|
| `run_id` | string | Run identifier; in the canonical retry table this is the consolidated run ID |
| `original_run_id` | string | Source pilot run that produced the original successes and failures |
| `consolidated_run_id` | string | Identifier joining original successes with targeted retry successes |
| `record_provenance` | enum | `ORIGINAL_SUCCESS` or `TARGETED_RETRY_SUCCESS` |
| `retry_id` | string | Retry operation that created the consolidated artifact |
| `retry_attempt` | integer | Zero for original successes; positive attempt number for retried rows |
| `original_failure_execution_order` | integer/null | Original shuffled task position for a retried cell |
| `original_failure_error_type` | string/null | Sanitized original error class for a retried cell |
| `retry_max_output_tokens` | integer/null | Output-token limit used for a targeted retry |
| `retry_attempted_at_utc` | timestamp/null | Time the targeted retry was sent |
| `execution_order` | integer | Shuffled execution position within the run |
| `assessment_created_at_utc` | timestamp | Time the successful assessment was stored |
| `symbol`, `company`, `sector` | string | Security identity supplied to the experiment |
| `condition` | enum | `A_MEMORY_ONLY`, `B_PRICE_HISTORY`, `C_QUANTITATIVE`, or `D_CURRENT_WEB` |
| `repetition` | integer | Repeat number within security and condition |
| `backend` | string | `OPENAI_RESPONSES_API` or `DRY_RUN_NOT_AI` |
| `model` | string/null | Model identifier returned by the live API |
| `requested_model` | string/null | Model identifier requested by configuration |
| `response_id` | string/null | OpenAI response identifier |
| `prompt_version` | string | `v1.4.0` for the archived full run; `v1.4.1` for next-run templates; `v1.3.0` for the corrected July pilot; `v1.2.0` is historical |
| `prompt_sha256` | string | SHA-256 of the exact rendered prompt |
| `packet_sha256` | string | SHA-256 of the condition data packet |

## Classification and explanation fields

| Field | Type | Definition |
|---|---|---|
| `rating` | enum | AI or dry-run classification on the five-level scale |
| `rating_score` | integer | `STRONG_BUY=2`, `BUY=1`, `HOLD=0`, `SELL=-1`, `STRONG_SELL=-2` |
| `confidence_0_100` | integer | Model-reported confidence; not an empirical probability |
| `evidence_sufficiency` | enum | `LOW`, `MEDIUM`, or `HIGH` model assessment of the permitted evidence |
| `forced_rating_due_to_insufficient_data` | boolean | Whether the required label was mainly a forced choice because evidence was inadequate |
| `reason_codes` | JSON array | One to five standardized explanation categories |
| `thesis` | JSON array | Exactly three concise decision points |
| `risks` | JSON array | Exactly three concise risks |
| `evidence` | JSON array | Up to 12 structured evidence items |
| `limitations` | JSON array | Model-disclosed limitations |

## Evidence-item fields

| Field | Type | Definition |
|---|---|---|
| `claim` | string | Claim supported by the evidence item |
| `category` | enum | Standardized reason/evidence category |
| `origin` | enum | `SUPPLIED_PRICE`, `SUPPLIED_TECHNICAL`, `SUPPLIED_FUNDAMENTAL`, `WEB_PRIMARY`, `WEB_SECONDARY`, `MODEL_MEMORY`, or `OTHER` |
| `observation_date` | date/null | Date on which the supplied metric or measured event was observed |
| `publication_date` | date/null | Publication date for web evidence; null for supplied packets unless independently published |
| `source_url` | string/null | URL for web evidence; normally null for supplied data and model memory |

## Web and analyst-exposure audit fields

| Field | Type | Definition |
|---|---|---|
| `web_search_enabled` | boolean | True only for a successful live Condition D assessment |
| `retrieved_source_urls` | JSON array | URLs extracted from the full API response |
| `web_search_call_count` | integer | Number of web-search calls captured in the response |
| `web_search_source_count` | integer | Total sources reported across captured web actions |
| `analyst_opinions_encountered` | boolean | Model self-report that permitted inputs or search exposed analyst opinions |
| `analyst_opinions_used` | boolean | Model self-report that analyst opinions influenced the rating |
| `analyst_terms_in_final_output` | enum | Whether analyst-related terms were detected in D's evidence, thesis, or risks; non-D rows are `NOT_APPLICABLE_NO_WEB` |
| `analyst_terms_detected_in_web_actions` | boolean | Whether analyst-related terms appeared in captured search queries or sources |
| `analyst_web_action_matched_terms` | JSON array | Matched analyst-related terms from the web-action audit |

These fields are observable audits and self-reports. They do not expose private model reasoning or prove independence.

## Token fields

| Field | Type | Definition |
|---|---|---|
| `input_tokens` | integer/null | Input usage reported by the live API |
| `output_tokens` | integer/null | Output usage reported by the live API |
| `total_tokens` | integer/null | Total usage reported by the live API; null for dry-run records. On recommendation rows this is usage for successful assessments only |

## Failed-attempt audit fields

These fields appear in hardened `assessment_errors.csv` and
`retry_attempts.csv` outputs. Older pilot failures predate this capture and
therefore remain explicitly unknown rather than being estimated.

| Field | Type | Definition |
|---|---|---|
| `paid_response_returned` | boolean | Whether a provider response payload was available after the attempt failed validation |
| `usage_complete` | boolean | Whether input tokens, output tokens, and web-search call count were all captured for cost accounting |
| `response_id`, `model` | string/null | Provider response identifier and returned model when available |
| `input_tokens`, `output_tokens`, `total_tokens` | integer/null | Usage from the failed response; these values are distinct from successful recommendation-row usage |
| `web_search_call_count`, `web_search_source_count` | integer/null | Web activity preserved from the failed response |
| `retrieved_source_urls` | JSON array | URLs extracted from the failed provider response |
| `failed_response_raw_artifact` | path/null | Raw returned response, stored separately from the sanitized error record |
| `retry_error_artifact` | path/null | Sanitized retry error record |
| `retry_raw_artifact` | path | Successful response, failed response, or error artifact representing the attempt |

## Analyst consensus fields

| Field | Type | Definition |
|---|---|---|
| `strong_buy`, `buy`, `hold`, `sell`, `strong_sell` | integer | Current Yahoo recommendation-distribution counts |
| `analyst_count` | integer | Sum of the five current distribution counts |
| `count_normalized_score` | number/null | Researcher-defined weighted mean on the −2 to +2 scale |
| `count_normalized_rating` | enum/null | Count-normalized score mapped to the five-level rating scale |
| `portal_rating_yahoo` | enum/null | Separately mapped Yahoo portal recommendation key |
| `portal_recommendation_key_yahoo` | string/null | Raw Yahoo portal recommendation label |
| `portal_recommendation_mean_yahoo` | number/null | Yahoo portal recommendation mean |
| `portal_analyst_count_yahoo` | integer/null | Yahoo portal number of analyst opinions |
| `consensus_period` | string/null | Recommendation-distribution period, expected to be `0m` for the current row |
| `price_target_current` | number/null | Current security price accompanying the Yahoo target summary |
| `price_target_low`, `price_target_mean`, `price_target_median`, `price_target_high` | number/null | Yahoo analyst price-target summary |
| `retrieved_at_utc` | timestamp/null | Time the current Yahoo analyst snapshot was retrieved |
| `raw_consensus` | JSON string | Normalized raw analyst payload retained in the CSV |

The count-normalized and portal baselines can disagree and must remain separate.

## AI-versus-analyst comparison fields

| Field | Type | Definition |
|---|---|---|
| `ordinal_gap_vs_count_normalized_analyst` | integer/null | AI score minus the count-normalized analyst score category |
| `exact_match_count_normalized_analyst` | boolean/null | Exact five-category match with the count-normalized baseline |
| `ordinal_gap_vs_portal_yahoo` | integer/null | AI score minus the Yahoo portal rating score |
| `exact_match_portal_yahoo` | boolean/null | Exact five-category match with the Yahoo portal baseline |
| `rating_differs_from_count_normalized_without_reported_analyst_use_and_sufficient_evidence` | boolean/null | AI differs from the count baseline, reports no analyst use, has medium/high evidence, and was not forced by insufficient data |
| `rating_differs_from_portal_without_reported_analyst_use_and_sufficient_evidence` | boolean/null | Same observable marker against the Yahoo portal baseline |

A true disagreement marker means the AI differs from a direct analyst-rating baseline under the recorded sufficient-evidence screen. For the v1.2.0 pilot it is exploratory, not proof of independence, because the C/D packet included analyst-derived forward P/E.

## Data-quality fields

| Field | Type | Definition |
|---|---|---|
| `price_observations` | integer | Number of retained daily adjusted-price rows |
| `price_data_start`, `price_data_end` | date | First and last retained price observation |
| `price_lag_days` | integer | Configured cutoff minus latest price date |
| `fundamental_fields` | integer | Count of compact fundamental packet fields excluding metadata and locally derived block |
| `missing_fundamental_metrics` | JSON array | Requested Yahoo metrics without a valid observation by the cutoff |
| `research_errors` | JSON object | Yahoo research-provider errors for the security |
| `research_data_cutoff` | date/null | Cutoff recorded by the research provider |
| `sector`, `sector_source`, `sector_etf` | string/null | Resolved GICS sector, its source, and the configured sector benchmark |
| `sector_resolved`, `sector_etf_available` | boolean | Full-universe sector completeness checks |
| `analyst_count_current_distribution` | integer | Current recommendation-distribution count used for analyst-benchmark coverage, not model-input eligibility |
| `portal_rating_available` | boolean | Whether the separate Yahoo portal baseline is present |

For every OpenAI run, the acquisition gate fails on fewer than 253 prices, future prices, more than seven days of lag, unresolved sectors or ETF mappings, stale or incomplete SPY/sector context, excessive missing fundamentals, fewer than eight fundamental fields, any research error, an empty current distribution, or a missing portal rating.

## Core feature fields

Returns, volatility, SMA distances, drawdown, and relative returns are decimals: `0.10` means 10%.

| Field | Definition |
|---|---|
| `return_1m`, `return_3m`, `return_6m`, `return_12m` | Adjusted-close return over 21, 63, 126, and 252 trading days |
| `relative_return_vs_spy_12m` | Stock 12-month return minus SPY 12-month return |
| `relative_return_vs_sector_12m` | Stock 12-month return minus configured sector ETF return |
| `volatility_20d_annualized`, `volatility_60d_annualized` | Daily-return standard deviation × √252 |
| `sma20`, `sma50`, `sma200` | Simple moving average of adjusted close |
| `distance_from_sma*` | Latest adjusted close / SMA − 1 |
| `rsi14` | 14-period exponentially smoothed relative strength index |
| `macd`, `macd_signal` | EMA12 − EMA26 and its 9-period EMA |
| `maximum_drawdown_52w` | Minimum trailing price / running peak − 1 over 252 trading days |
| `position_in_52w_range` | Relative location between 52-week low and high |
| `volume_zscore_20d` | Latest volume relative to its trailing 20-observation mean and standard deviation |
| `beta_vs_spy_1y` | Covariance(stock, SPY) / variance(SPY) over up to 252 aligned returns |

## Failure and manifest fields

| Field | Type | Definition |
|---|---|---|
| `planned_assessments` | integer | Total tasks generated before execution |
| `completed_assessments` | integer | Successful assessments |
| `failed_assessments` | integer | Attempted assessments that failed |
| `not_attempted_assessments` | integer | Tasks skipped after a failure-threshold abort |
| `aborted_after_failure_threshold` | boolean | Whether execution stopped after repeated failures |
| `consecutive_failure_limit` | integer | Configured consecutive-failure stop threshold; currently 3 |
| `total_failure_limit` | integer | Configured total-failure stop threshold; currently 6 |
| `total_tokens` | integer | Sum of successful assessment token use; failed-response usage is separate |
| `failed_response_input_tokens`, `failed_response_output_tokens`, `failed_response_total_tokens` | integer | Recorded usage from failed original and retry responses |
| `failed_response_web_search_calls` | integer | Recorded billable web-search calls from failed responses |
| `recorded_total_tokens_including_failures` | integer | Successful tokens plus recorded failed-response tokens |
| `failed_call_usage` | JSON object | Combined failed-call count, complete/partial usage counts, archived-response count, tokens, web calls, and unknown count |
| `original_failed_call_usage`, `retry_failed_call_usage` | JSON object | Same accounting split between source-run failures and new retry failures |
| `original_successful_assessments` | integer | Successful cells copied from the original run into a consolidated retry artifact |
| `targeted_retry_requested` | integer | Original failed cells selected for exact retry |
| `targeted_retry_succeeded` | integer | Requested cells successfully repaired |
| `targeted_retry_failed` | integer | Requested cells still unresolved |
| `original_known_success_tokens` | integer | Recorded tokens from original successful cells |
| `retry_known_success_tokens` | integer | Recorded tokens from successful retries |
| `known_original_failed_call_usage_count`, `known_retry_failed_call_usage_count` | integer | Failed calls with complete recorded cost inputs |
| `unknown_original_failed_call_usage_count`, `unknown_retry_failed_call_usage_count` | integer | Failed calls still missing at least one required cost input |
| `unknown_paid_failure_usage_count` | integer | Combined paid failed calls whose complete usage was not archived |
| `record_provenance_values` | JSON array | Provenance categories present in the consolidated recommendation rows |
| `source_artifact_sha256` | JSON object | Hashes of source-run artifacts used in consolidation |

## `pilot_summary.json` sections

| Section | Meaning |
|---|---|
| `run` | Canonical run ID, model, cutoff, symbols, conditions, and timestamps |
| `completion` | Planned, completed, failed, and not-attempted cells plus consistency checks |
| `ratings` | Overall and condition rating distributions plus all stock-condition-repetition ratings |
| `quality` | Confidence, evidence sufficiency, and forced-rating summaries |
| `stability` | Modal rating, agreement rate, unanimity, and rating-score dispersion for each of 24 stock-condition groups |
| `condition_transitions` | Direction and frequency of rating changes for every condition pair |
| `analyst_comparison` | Separate count-normalized and Yahoo portal baseline comparisons |
| `analyst_exposure_audit` | Model-reported encounter/use plus captured web-action and structured evidence/thesis/risk term audits |
| `web_usage` | Recorded web calls, source references, and unique captured URLs/domains |
| `token_usage` | Recorded input/output/total tokens and web calls across successful assessments plus known failed responses, with the two sources broken out and missing paid-failure usage counted |
| `estimated_api_cost` | Pricing assumptions, successful-attempt and failed-attempt known cost components, exact/lower-bound status, and missing-usage warning |
| `provenance` | Artifacts used, credential-file count, and warnings |

Cost fields use decimal-string dollar amounts to avoid binary floating-point
presentation errors. Successful recommendation rows and `failed_call_usage`
are summed once each; when the manifest already contains the aggregate failed
usage, error-table rows are not added again. Official assumptions came from
the [GPT-5.4 model page](https://developers.openai.com/api/docs/models/gpt-5.4)
and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing).
`lower_bound_total_cost_usd` is not an invoice or budget guarantee.
