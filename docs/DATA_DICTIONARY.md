# Data dictionary

## Run-level files

| File | Grain | Meaning |
|---|---|---|
| `manifest.json` | One run | Frozen scope, backend, date, symbols, conditions, repetition count, providers, model, key availability, and disclaimer |
| `experiment_universe.csv` | One security | The securities actually evaluated |
| `universe_snapshot/spy_holdings.xlsx` | One source snapshot | Unmodified dated State Street workbook |
| `universe_snapshot/spy_holdings.csv` | One holding | Parsed audit snapshot, excluding cash and temporary contra receivables |
| `features.csv` | One security | Locally calculated market/technical feature packet |
| `analyst_consensus.csv` | One security | Raw counts, normalized score/rating, and raw provider payload; header-only when the key is absent |
| `recommendations.jsonl` | One assessment | Canonical nested assessment plus run identifiers |
| `recommendations.csv` | One assessment | Flat analysis-ready representation |
| `condition_changes.csv` | One condition pair | Pairwise flip rate and mean absolute ordinal change |
| `prompts/*.txt` | One assessment | Exact rendered prompt supplied or prepared |
| `raw_responses/*.json` | One assessment | Provider response for audit and re-parsing |

## Recommendation fields

| Field | Type | Definition |
|---|---|---|
| `symbol` | string | Security ticker used by the experiment |
| `condition` | enum | `A_MEMORY_ONLY`, `B_PRICE_HISTORY`, `C_QUANTITATIVE`, or `D_CURRENT_WEB` |
| `repetition` | integer | Repeat number within security and condition |
| `rating` | enum | AI/dry-run classification on the five-level scale |
| `rating_score` | integer | `STRONG_BUY=2`, `BUY=1`, `HOLD=0`, `SELL=-1`, `STRONG_SELL=-2` |
| `confidence_0_100` | integer | Model-reported confidence; not an empirical probability |
| `reason_codes` | JSON array | Standardized explanation categories |
| `thesis` | JSON array | Exactly three concise favorable/decision points |
| `risks` | JSON array | Exactly three concise risks |
| `evidence` | JSON array | Claim, category, publication date, and source URL |
| `analyst_opinions_encountered` | boolean | Whether the model saw analyst views in its permitted inputs/search |
| `analyst_opinions_used` | boolean | Whether the model reports that they influenced the rating |
| `backend` | string | `OPENAI_RESPONSES_API` or `DRY_RUN_NOT_AI` |
| `web_search_enabled` | boolean | True only for a live Condition D assessment |
| `input_tokens`, `output_tokens`, `total_tokens` | integer/null | Usage reported by the live API; null for the dry-run backend |
| `analyst_rating` | enum/null | Consensus counts normalized onto the five-level scale |
| `ordinal_gap_vs_analyst` | integer/null | AI rating score minus analyst rating score |
| `exact_match_analyst` | boolean/null | Whether the five-category labels are identical |

## Core feature fields

Returns, volatility, SMA distances, drawdown, and relative returns are stored as decimals: `0.10` means 10%.

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
| `volume_zscore_20d` | Latest volume relative to its trailing 20-observation mean/std. dev. |
| `beta_vs_spy_1y` | Covariance(stock, SPY) / variance(SPY) over up to 252 aligned returns |
