#!/usr/bin/env python3
"""Build a denominator-explicit interim or final analysis of an official run."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from sp500_ai.analysis import RATING_SCORE, attach_analyst_comparison


CONDITION_ORDER = [
    "A_MEMORY_ONLY",
    "B_PRICE_HISTORY",
    "C_QUANTITATIVE",
    "D_CURRENT_WEB",
]
ADJACENT_PAIRS = list(zip(CONDITION_ORDER, CONDITION_ORDER[1:]))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, newline=""
    ) as handle:
        frame.to_csv(handle, index=False)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _accepted_records(run_dir: Path, state: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for task_id, task in state["tasks"].items():
        if task.get("status") != "ACCEPTED":
            continue
        checkpoint_path = run_dir / str(task["checkpoint_path"])
        checkpoint = _read_json(checkpoint_path)
        if checkpoint.get("status") != "ACCEPTED":
            raise ValueError(f"Accepted state has non-accepted checkpoint: {task_id}")
        record = dict(checkpoint["record"])
        record["task_id"] = task_id
        rows.append(record)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if frame["task_id"].duplicated().any():
        raise ValueError("Accepted task IDs are duplicated")
    frame["rating_score"] = frame["rating"].map(RATING_SCORE)
    if frame["rating_score"].isna().any():
        raise ValueError("An accepted rating is outside the ordinal scale")
    return frame.sort_values(["condition", "symbol", "repetition"]).reset_index(drop=True)


def _condition_summary(
    frame: pd.DataFrame, registry: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for condition in CONDITION_ORDER:
        subset = frame.loc[frame["condition"] == condition].copy()
        planned = int((registry["condition"] == condition).sum())
        ratings = subset["rating"].value_counts()
        row: dict[str, Any] = {
            "condition": condition,
            "planned": planned,
            "accepted": int(len(subset)),
            "accepted_coverage_pct": round(100 * len(subset) / planned, 3) if planned else None,
            "mean_rating_score": float(subset["rating_score"].mean()) if len(subset) else None,
            "mean_confidence": float(subset["confidence_0_100"].mean()) if len(subset) else None,
            "forced_insufficient_pct": (
                float(100 * subset["forced_rating_due_to_insufficient_data"].astype(bool).mean())
                if len(subset)
                else None
            ),
        }
        for rating in ["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"]:
            count = int(ratings.get(rating, 0))
            row[f"{rating.lower()}_count"] = count
            row[f"{rating.lower()}_pct"] = round(100 * count / len(subset), 3) if len(subset) else None
        rows.append(row)
    return pd.DataFrame(rows)


def _stability_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for condition in CONDITION_ORDER:
        subset = frame.loc[frame["condition"] == condition]
        complete = [group for _, group in subset.groupby("symbol") if len(group) == 3]
        exact = [group["rating"].nunique() == 1 for group in complete]
        modal = [group["rating"].value_counts().max() / 3 for group in complete]
        ranges = [group["confidence_0_100"].max() - group["confidence_0_100"].min() for group in complete]
        rows.append(
            {
                "condition": condition,
                "complete_three_run_symbols": len(complete),
                "exact_three_run_stability_pct": float(100 * sum(exact) / len(exact)) if exact else None,
                "mean_modal_agreement_pct": float(100 * sum(modal) / len(modal)) if modal else None,
                "mean_confidence_range_points": float(sum(ranges) / len(ranges)) if ranges else None,
            }
        )
    return pd.DataFrame(rows)


def _matched_stability_summary(frame: pd.DataFrame) -> pd.DataFrame:
    complete_d = {
        symbol
        for symbol, group in frame.loc[frame["condition"] == "D_CURRENT_WEB"].groupby("symbol")
        if len(group) == 3
    }
    rows: list[dict[str, Any]] = []
    for condition in CONDITION_ORDER:
        subset = frame.loc[
            (frame["condition"] == condition) & frame["symbol"].isin(complete_d)
        ]
        groups = [group for _, group in subset.groupby("symbol") if len(group) == 3]
        exact = [group["rating"].nunique() == 1 for group in groups]
        modal = [group["rating"].value_counts().max() / 3 for group in groups]
        rows.append(
            {
                "condition": condition,
                "matched_complete_three_run_symbols": len(groups),
                "exact_three_run_stability_pct": float(100 * sum(exact) / len(exact)) if exact else None,
                "mean_modal_agreement_pct": float(100 * sum(modal) / len(modal)) if modal else None,
            }
        )
    return pd.DataFrame(rows)


def _transition_summary(frame: pd.DataFrame) -> pd.DataFrame:
    score = frame.pivot_table(
        index=["symbol", "repetition"], columns="condition", values="rating_score", aggfunc="first"
    )
    confidence = frame.pivot_table(
        index=["symbol", "repetition"], columns="condition", values="confidence_0_100", aggfunc="first"
    )
    rows: list[dict[str, Any]] = []
    for left, right in ADJACENT_PAIRS:
        pair = score[[left, right]].dropna()
        conf_pair = confidence[[left, right]].dropna()
        delta = pair[right] - pair[left]
        rows.append(
            {
                "left_condition": left,
                "right_condition": right,
                "paired_assessments": int(len(pair)),
                "rating_change_pct": float(100 * (delta != 0).mean()) if len(pair) else None,
                "upgrade_pct": float(100 * (delta > 0).mean()) if len(pair) else None,
                "downgrade_pct": float(100 * (delta < 0).mean()) if len(pair) else None,
                "mean_ordinal_change": float(delta.mean()) if len(pair) else None,
                "mean_absolute_ordinal_change": float(delta.abs().mean()) if len(pair) else None,
                "mean_confidence_change_points": (
                    float((conf_pair[right] - conf_pair[left]).mean()) if len(conf_pair) else None
                ),
            }
        )
    return pd.DataFrame(rows)


def _analyst_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for condition in CONDITION_ORDER:
        subset = frame.loc[frame["condition"] == condition]
        count_available = subset["count_normalized_rating"].notna()
        portal_available = subset["portal_rating_yahoo"].notna()
        rows.append(
            {
                "condition": condition,
                "accepted": int(len(subset)),
                "count_baseline_available": int(count_available.sum()),
                "count_exact_match_pct": (
                    float(100 * subset.loc[count_available, "exact_match_count_normalized_analyst"].astype(bool).mean())
                    if count_available.any()
                    else None
                ),
                "count_mean_absolute_ordinal_gap": (
                    float(subset.loc[count_available, "ordinal_gap_vs_count_normalized_analyst"].abs().mean())
                    if count_available.any()
                    else None
                ),
                "portal_baseline_available": int(portal_available.sum()),
                "portal_exact_match_pct": (
                    float(100 * subset.loc[portal_available, "exact_match_portal_yahoo"].astype(bool).mean())
                    if portal_available.any()
                    else None
                ),
                "portal_mean_absolute_ordinal_gap": (
                    float(subset.loc[portal_available, "ordinal_gap_vs_portal_yahoo"].abs().mean())
                    if portal_available.any()
                    else None
                ),
            }
        )
    return pd.DataFrame(rows)


def _paired_c_d_analyst_summary(frame: pd.DataFrame) -> pd.DataFrame:
    subset = frame.loc[frame["condition"].isin(["C_QUANTITATIVE", "D_CURRENT_WEB"])]
    rows: list[dict[str, Any]] = []
    for baseline_name, exact_column, gap_column in (
        (
            "count_normalized",
            "exact_match_count_normalized_analyst",
            "ordinal_gap_vs_count_normalized_analyst",
        ),
        ("portal_yahoo", "exact_match_portal_yahoo", "ordinal_gap_vs_portal_yahoo"),
    ):
        wide = subset.pivot(
            index=["symbol", "repetition"],
            columns="condition",
            values=[exact_column, gap_column],
        ).dropna()
        for condition in ("C_QUANTITATIVE", "D_CURRENT_WEB"):
            exact = wide[(exact_column, condition)].astype(str).str.lower().eq("true")
            rows.append(
                {
                    "analyst_baseline": baseline_name,
                    "condition": condition,
                    "paired_assessments": int(len(wide)),
                    "exact_match_pct": float(100 * exact.mean()) if len(wide) else None,
                    "mean_absolute_ordinal_gap": (
                        float(pd.to_numeric(wide[(gap_column, condition)]).abs().mean())
                        if len(wide)
                        else None
                    ),
                }
            )
    return pd.DataFrame(rows)


def _failure_summary(
    run_dir: Path, state: dict[str, Any], registry: pd.DataFrame, max_attempts: int
) -> pd.DataFrame:
    registry_lookup = registry.set_index("task_id").to_dict(orient="index")
    rows: list[dict[str, Any]] = []
    for task_id, task in state["tasks"].items():
        if task.get("status") != "FAILED":
            continue
        attempts = int(task.get("attempts") or 0)
        checkpoint_path = (
            run_dir
            / "checkpoints"
            / task_id.replace(":", "_")
            / f"attempt_{attempts}.json"
        )
        checkpoint = _read_json(checkpoint_path) if checkpoint_path.is_file() else {}
        raw: dict[str, Any] = {}
        raw_path = checkpoint.get("raw_path")
        if raw_path and (run_dir / str(raw_path)).is_file():
            loaded = _read_json(run_dir / str(raw_path))
            if isinstance(loaded, dict):
                raw = loaded
        response = raw.get("response") or {}
        nested_error = (response.get("body") or {}).get("error") or raw.get("error") or {}
        error_code = str(nested_error.get("code") or "") if isinstance(nested_error, dict) else ""
        error = str(task.get("last_error") or checkpoint.get("error") or "")
        if error_code in {"credit_balance_exhausted", "billing_hard_limit_reached"}:
            cause = "QUOTA_EXHAUSTED"
        elif "observation_date" in error and "after the assessment cutoff" in error:
            cause = "FUTURE_OBSERVATION_DATE"
        elif "source_url with a web origin" in error:
            cause = "INVALID_SOURCE_URL_ORIGIN"
        elif "SUPPLIED_TECHNICAL is not permitted in B_PRICE_HISTORY" in error:
            cause = "INVALID_B_EVIDENCE_ORIGIN"
        elif "requires observation_date" in error:
            cause = "MISSING_OBSERVATION_DATE"
        elif "JSONDecodeError" in error or "Unterminated string" in error:
            cause = "TRUNCATED_OR_INVALID_JSON"
        elif "BatchError" in error:
            cause = "BATCH_ERROR_OTHER"
        else:
            cause = "OTHER_VALIDATION_OR_BATCH_ERROR"
        if cause == "QUOTA_EXHAUSTED":
            recovery = (
                "ADD_CREDITS_AND_EXTRA_EXACT_RETRY"
                if attempts >= max_attempts
                else "ADD_CREDITS_AND_STANDARD_EXACT_RETRY"
            )
        else:
            recovery = (
                "VALIDATION_RETRY_EXHAUSTED"
                if attempts >= max_attempts
                else "STANDARD_EXACT_RETRY_NOT_SUBMITTED"
            )
        metadata = registry_lookup[task_id]
        rows.append(
            {
                "task_id": task_id,
                "symbol": metadata["symbol"],
                "condition": metadata["condition"],
                "repetition": int(metadata["repetition"]),
                "attempts": attempts,
                "failure_cause": cause,
                "error_code": error_code or None,
                "recovery_action": recovery,
                "last_error": error,
            }
        )
    columns = [
        "task_id",
        "symbol",
        "condition",
        "repetition",
        "attempts",
        "failure_cause",
        "error_code",
        "recovery_action",
        "last_error",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["failure_cause", "condition", "symbol", "repetition"]
    )


def _cost_summary(run_dir: Path) -> dict[str, Any]:
    total = Counter()
    by_condition: dict[str, Counter[str]] = {}
    successful_responses = 0
    for path in sorted((run_dir / "downloads").glob("*.output.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                line = json.loads(raw)
                body = ((line.get("response") or {}).get("body") or {})
                usage = body.get("usage") or {}
                parts = str(line.get("custom_id") or "").split(":")
                condition = parts[2] if len(parts) > 2 else "UNKNOWN"
                bucket = by_condition.setdefault(condition, Counter())
                values = {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "cached_input_tokens": int(
                        (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
                    ),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "web_search_calls": int(
                        (((body.get("tool_usage") or {}).get("web_search") or {}).get("num_requests"))
                        or 0
                    ),
                }
                for key, value in values.items():
                    total[key] += value
                    bucket[key] += value
                successful_responses += 1
    def estimate_cost(usage: Counter[str]) -> float:
        uncached = usage["input_tokens"] - usage["cached_input_tokens"]
        return (
            uncached * 1.25 / 1_000_000
            + usage["cached_input_tokens"] * 0.13 / 1_000_000
            + usage["output_tokens"] * 7.50 / 1_000_000
            + usage["web_search_calls"] * 0.01
        )

    estimated_cost = estimate_cost(total)
    condition_usage = {
        key: {**dict(value), "estimated_cost_usd": round(estimate_cost(value), 6)}
        for key, value in sorted(by_condition.items())
    }
    return {
        "pricing_basis": {
            "model": "gpt-5.4 batch short-context",
            "uncached_input_usd_per_million": 1.25,
            "cached_input_usd_per_million": 0.13,
            "output_usd_per_million": 7.50,
            "web_search_usd_per_call": 0.01,
            "source": "https://developers.openai.com/api/docs/pricing",
        },
        "downloaded_success_responses_including_retries": successful_responses,
        "usage": dict(total),
        "usage_by_condition_code": condition_usage,
        "estimated_billed_cost_usd": round(estimated_cost, 6),
        "cost_is_lower_bound": True,
    }


def analyze(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    state = _read_json(run_dir / "state.json")
    plan = _read_json(run_dir / "official_plan.json")
    registry = pd.read_json(run_dir / str(plan["task_registry"]), lines=True)
    records = _accepted_records(run_dir, state)
    consensus = pd.read_csv(run_dir / "frozen_inputs" / "eligible_analyst_consensus.csv")
    compared = attach_analyst_comparison(records, consensus)

    condition = _condition_summary(compared, registry)
    stability = _stability_summary(compared)
    matched_stability = _matched_stability_summary(compared)
    transitions = _transition_summary(compared)
    analyst = _analyst_summary(compared)
    paired_analyst = _paired_c_d_analyst_summary(compared)
    effective_max_attempts = int(plan["max_attempts_per_task"])
    recovery_plan_path = run_dir / "RECOVERY_PLAN.json"
    if recovery_plan_path.is_file():
        recovery_plan = _read_json(recovery_plan_path)
        if recovery_plan.get("parent_plan_sha256") == plan.get("plan_sha256"):
            effective_max_attempts = max(
                effective_max_attempts,
                int(recovery_plan.get("recovery_max_attempts_per_task") or 0),
            )
    failures = _failure_summary(
        run_dir, state, registry, effective_max_attempts
    )
    cost = _cost_summary(run_dir)

    task_counts = Counter(str(task.get("status")) for task in state["tasks"].values())
    d = compared.loc[compared["condition"] == "D_CURRENT_WEB"]
    summary = {
        "run_id": plan["run_id"],
        "as_of_date": plan["as_of_date"],
        "analysis_status": "COMPLETE" if task_counts.get("ACCEPTED", 0) == plan["planned_assessments"] else "PARTIAL",
        "run_status": state["status"],
        "planned_assessments": int(plan["planned_assessments"]),
        "task_counts": dict(task_counts),
        "accepted_coverage_pct": round(
            100 * task_counts.get("ACCEPTED", 0) / int(plan["planned_assessments"]), 3
        ),
        "eligible_securities": int(plan["security_count_eligible"]),
        "conditions": CONDITION_ORDER,
        "repetitions": int(plan["repetitions"]),
        "effective_max_attempts_per_task": effective_max_attempts,
        "model": plan["model"],
        "price_history_packet": f"{plan['price_history_observations']} {plan['price_history_frequency']} observations",
        "d_web_accepted": int(len(d)),
        "d_analyst_opinions_encountered_pct": (
            float(100 * d["analyst_opinions_encountered"].astype(bool).mean()) if len(d) else None
        ),
        "d_analyst_opinions_used_pct": (
            float(100 * d["analyst_opinions_used"].astype(bool).mean()) if len(d) else None
        ),
        "failure_counts_by_cause": (
            failures["failure_cause"].value_counts().sort_index().to_dict()
            if len(failures)
            else {}
        ),
        "cost": cost,
        "interpretation_guardrails": [
            "Analyst agreement is a benchmark comparison, not realized-return accuracy.",
            "The 12-month target cannot be scored for predictive accuracy until the prospective horizon ends.",
            "Condition-D statistics are provisional while condition-D coverage is incomplete.",
            "Quota errors and schema rejections are operational missingness, not model ratings.",
        ],
    }

    _atomic_csv(output_dir / "accepted_assessments.csv", compared)
    _atomic_csv(output_dir / "condition_summary.csv", condition)
    _atomic_csv(output_dir / "stability_summary.csv", stability)
    _atomic_csv(output_dir / "matched_stability_summary.csv", matched_stability)
    _atomic_csv(output_dir / "adjacent_condition_transitions.csv", transitions)
    _atomic_csv(output_dir / "analyst_comparison_summary.csv", analyst)
    _atomic_csv(output_dir / "paired_c_d_analyst_summary.csv", paired_analyst)
    _atomic_csv(output_dir / "failure_summary.csv", failures)
    _atomic_json(output_dir / "cost_summary.json", cost)
    _atomic_json(output_dir / "analysis_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else run_dir / "analysis_interim"
    )
    print(json.dumps(analyze(run_dir, output_dir), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
