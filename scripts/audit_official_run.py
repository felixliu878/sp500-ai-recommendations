#!/usr/bin/env python3
"""Offline audit of a finished official run.

The audit reconciles archived records, adds security-clustered bootstrap
intervals, profiles missingness, and semantically reviews the evidence that
caused the remaining rejections. It reads archived state, checkpoints, raw
responses, and the canonical analysis. It never contacts a model provider and
never edits run state or the canonical analysis.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import shutil
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd

from sp500_ai.analysis import RATING_SCORE, attach_analyst_comparison
from sp500_ai.llm import WEB_EVIDENCE_ORIGINS, validate_assessment
from sp500_ai.official import _extract_output_text

try:
    from scripts.analyze_official_progress import (
        CONDITION_ORDER,
        _atomic_csv,
        _atomic_json,
        _read_json,
    )
except ModuleNotFoundError:  # Executed as scripts/audit_official_run.py.
    from analyze_official_progress import (  # type: ignore[no-redef]
        CONDITION_ORDER,
        _atomic_csv,
        _atomic_json,
        _read_json,
    )


CANONICAL_ANALYSIS = "analysis_final_attempt_limited"
PROTECTED_OUTPUTS = {CANONICAL_ANALYSIS, "analysis_interim"}
CODE = {
    "A_MEMORY_ONLY": "A",
    "B_PRICE_HISTORY": "B",
    "C_QUANTITATIVE": "C",
    "D_CURRENT_WEB": "D",
}
CONDITION_PAIRS = [
    ("A_MEMORY_ONLY", "B_PRICE_HISTORY"),
    ("B_PRICE_HISTORY", "C_QUANTITATIVE"),
    ("C_QUANTITATIVE", "D_CURRENT_WEB"),
    ("A_MEMORY_ONLY", "C_QUANTITATIVE"),
    ("B_PRICE_HISTORY", "D_CURRENT_WEB"),
    ("A_MEMORY_ONLY", "D_CURRENT_WEB"),
]
ADJACENT = {pair for pair in zip(CONDITION_ORDER, CONDITION_ORDER[1:])}
BOOLEAN_COLUMNS = [
    "analyst_opinions_encountered",
    "analyst_opinions_used",
    "forced_rating_due_to_insufficient_data",
    "exact_match_count_normalized_analyst",
    "exact_match_portal_yahoo",
]
BASELINES = {
    "count_normalized": "exact_match_count_normalized_analyst",
    "portal_yahoo": "exact_match_portal_yahoo",
}
DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 20261029

# Ordered: guidance language is checked before event language.
FORWARD_GUIDANCE = re.compile(
    r"\b(guid\w*|outlook|forecast\w*|expect\w*|projected|target\w*|estimat\w*)\b",
    re.IGNORECASE,
)
PRE_ANNOUNCED_EVENT = re.compile(
    r"\b(vote\w*|special meeting|launch\w*|effective|scheduled|final rule)\b",
    re.IGNORECASE,
)

SHAREABLE_FILES = [
    "reconciliation.json",
    "condition_pair_transitions.csv",
    "bootstrap_intervals.csv",
    "missingness_by_sector.csv",
    "missingness_security_profile.csv",
    "failure_audit_summary.json",
    "sensitivity_alternative_date_rule.csv",
    "accepted_d_guidance_dating.json",
    "audit_summary.json",
]


def parse_bool(value: Any) -> Any:
    """Parse CSV Boolean text explicitly; ``bool("False")`` would be true."""
    if value is None or value is pd.NA:
        return pd.NA
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, float) and math.isnan(value):
        return pd.NA
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    if text in {"", "nan", "<na>", "none"}:
        return pd.NA
    raise ValueError(f"Unrecognized Boolean value: {value!r}")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def load_accepted(analysis_dir: Path) -> pd.DataFrame:
    frame = pd.read_csv(analysis_dir / "accepted_assessments.csv")
    for column in BOOLEAN_COLUMNS:
        frame[column] = frame[column].map(parse_bool)
    expected_scores = frame["rating"].map(RATING_SCORE)
    if expected_scores.isna().any() or (expected_scores != frame["rating_score"]).any():
        raise ValueError("Accepted rating_score does not match the ordinal encoding")
    return frame


def _effective_max_attempts(run_dir: Path, plan: dict[str, Any]) -> int:
    maximum = int(plan["max_attempts_per_task"])
    recovery_path = run_dir / "RECOVERY_PLAN.json"
    if recovery_path.is_file():
        recovery = _read_json(recovery_path)
        if recovery.get("parent_plan_sha256") == plan.get("plan_sha256"):
            maximum = max(maximum, int(recovery.get("recovery_max_attempts_per_task") or 0))
    return maximum


def _task_dir(run_dir: Path, task_id: str) -> Path:
    return run_dir / "checkpoints" / task_id.replace(":", "_")


def _response_body(run_dir: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    raw_path = checkpoint.get("raw_path")
    if not raw_path or not (run_dir / str(raw_path)).is_file():
        return {}
    raw = _read_json(run_dir / str(raw_path))
    if not isinstance(raw, dict):
        return {}
    # Accepted attempts archive the response body itself; failed attempts
    # archive the whole Batch output line.
    if "response" in raw or "custom_id" in raw:
        return ((raw.get("response") or {}).get("body")) or {}
    return raw


def iter_attempts(run_dir: Path, task_id: str, attempts: int) -> Iterator[dict[str, Any]]:
    """Yield each archived attempt with its structured payload, when one exists."""
    for attempt in range(1, attempts + 1):
        path = _task_dir(run_dir, task_id) / f"attempt_{attempt}.json"
        checkpoint = _read_json(path)
        body = _response_body(run_dir, checkpoint)
        payload = checkpoint.get("assessment")
        payload_status = "STRUCTURED" if payload is not None else "NO_OUTPUT_TEXT"
        if payload is None and body:
            text = _extract_output_text(body)
            if text:
                try:
                    loaded = json.loads(text)
                except json.JSONDecodeError:
                    loaded = None
                payload = loaded if isinstance(loaded, dict) else None
                payload_status = "STRUCTURED" if payload is not None else "UNPARSEABLE_OUTPUT_TEXT"
        yield {
            "attempt": attempt,
            "checkpoint": checkpoint,
            "body": body,
            "payload": payload,
            "payload_status": payload_status,
        }


def replay_validation(
    payload: dict[str, Any], *, symbol: str, condition: str, plan: dict[str, Any]
) -> str | None:
    """Repeat ingestion's identity and evidence checks; return the error text."""
    expected = {
        "symbol": symbol,
        "as_of": plan["as_of_date"],
        "horizon_months": int(plan["horizon_months"]),
    }
    mismatches = {
        key: {"expected": value, "received": payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if mismatches:
        return f"ValueError: Assessment identity mismatch: {mismatches}"
    try:
        validate_assessment(
            payload,
            cutoff=str(plan["as_of_date"]),
            allow_web=condition == "D_CURRENT_WEB",
            condition=condition,
        )
    except ValueError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _check(name: str, expected: Any, observed: Any) -> dict[str, Any]:
    return {"check": name, "expected": expected, "observed": observed, "passed": expected == observed}


def reconcile(
    run_dir: Path,
    accepted: pd.DataFrame,
    failures: pd.DataFrame,
    compare_dir: Path | None = None,
) -> dict[str, Any]:
    plan = _read_json(run_dir / "official_plan.json")
    state = _read_json(run_dir / "state.json")
    registry_path = run_dir / str(plan["task_registry"])
    registry = pd.read_json(registry_path, lines=True)
    planned = int(plan["planned_assessments"])
    per_condition = planned // len(CONDITION_ORDER)
    max_attempts = _effective_max_attempts(run_dir, plan)
    checks: list[dict[str, Any]] = []

    checks.append(_check("task_registry_sha256", plan["task_registry_sha256"], _sha256_file(registry_path)))
    checks.append(_check("state_plan_sha256", plan["plan_sha256"], state["plan_sha256"]))
    checks.append(_check("registry_rows", planned, int(len(registry))))
    checks.append(_check("registry_unique_task_ids", planned, int(registry["task_id"].nunique())))
    checks.append(
        _check(
            "registry_unique_security_condition_repetition_cells",
            planned,
            int(len(registry.drop_duplicates(["symbol", "condition", "repetition"]))),
        )
    )
    checks.append(_check("registry_securities", int(plan["security_count_eligible"]), int(registry["symbol"].nunique())))
    counts = registry["condition"].value_counts()
    checks.append(
        _check(
            "registry_tasks_per_condition",
            {condition: per_condition for condition in CONDITION_ORDER},
            {condition: int(counts.get(condition, 0)) for condition in CONDITION_ORDER},
        )
    )
    packets = registry.pivot(index=["symbol", "repetition"], columns="condition", values="packet_sha256")
    checks.append(
        _check(
            "d_packet_identical_to_c_packet",
            int(len(packets)),
            int((packets["C_QUANTITATIVE"] == packets["D_CURRENT_WEB"]).sum()),
        )
    )
    checks.append(_check("state_task_ids_match_registry", True, set(state["tasks"]) == set(registry["task_id"])))
    status_counts = Counter(str(task.get("status")) for task in state["tasks"].values())
    checks.append(_check("state_tasks_accepted_or_failed", planned, status_counts["ACCEPTED"] + status_counts["FAILED"]))

    lookup = registry.set_index("task_id")
    problems: Counter[str] = Counter()
    replay = Counter()
    checkpoint_files = 0
    accepted_attempt_distribution: Counter[int] = Counter()
    for task_id, task in state["tasks"].items():
        meta = lookup.loc[task_id]
        attempts = int(task.get("attempts") or 0)
        files = sorted(_task_dir(run_dir, task_id).glob("attempt_*.json"))
        checkpoint_files += len(files)
        if len(files) != attempts:
            problems["attempt_file_count_differs_from_state"] += 1
        statuses = []
        for item in iter_attempts(run_dir, task_id, attempts):
            checkpoint = item["checkpoint"]
            statuses.append(checkpoint.get("status"))
            payload = item["payload"]
            if payload is None:
                replay[item["payload_status"].lower()] += 1
                continue
            error = replay_validation(
                payload, symbol=str(meta["symbol"]), condition=str(meta["condition"]), plan=plan
            )
            if checkpoint.get("status") == "ACCEPTED":
                replay["accepted_reproduced" if error is None else "accepted_now_rejected"] += 1
            elif error is not None and error == checkpoint.get("error"):
                replay["rejection_reproduced"] += 1
            else:
                replay["rejection_not_reproduced"] += 1
        if task.get("status") == "ACCEPTED":
            accepted_attempt_distribution[int(task["accepted_attempt"])] += 1
            if statuses.count("ACCEPTED") != 1:
                problems["accepted_task_without_exactly_one_accepted_checkpoint"] += 1
            if int(task["accepted_attempt"]) != attempts or statuses[-1] != "ACCEPTED":
                problems["accepted_checkpoint_is_not_final_attempt"] += 1
            if task["checkpoint_path"] != f"checkpoints/{task_id.replace(':', '_')}/attempt_{attempts}.json":
                problems["accepted_checkpoint_path_mismatch"] += 1
            record = _read_json(run_dir / task["checkpoint_path"])["record"]
            for field in ("symbol", "condition", "repetition", "prompt_sha256", "packet_sha256"):
                if record.get(field) != meta[field]:
                    problems[f"accepted_record_{field}_mismatch"] += 1
        else:
            if "ACCEPTED" in statuses:
                problems["failed_task_has_accepted_checkpoint"] += 1
            if attempts != max_attempts:
                problems["failed_task_below_attempt_ceiling"] += 1
    checks.append(_check("checkpoint_files", sum(int(t.get("attempts") or 0) for t in state["tasks"].values()), checkpoint_files))
    checks.append(_check("checkpoint_integrity_problems", {}, dict(problems)))
    checks.append(_check("validator_replay_accepted_now_rejected", 0, replay["accepted_now_rejected"]))
    checks.append(_check("validator_replay_rejection_not_reproduced", 0, replay["rejection_not_reproduced"]))

    accepted_ids = {task_id for task_id, task in state["tasks"].items() if task.get("status") == "ACCEPTED"}
    failed_ids = {task_id for task_id, task in state["tasks"].items() if task.get("status") == "FAILED"}
    checks.append(_check("accepted_csv_rows", len(accepted_ids), int(len(accepted))))
    checks.append(_check("accepted_csv_unique_task_ids", len(accepted_ids), int(accepted["task_id"].nunique())))
    checks.append(_check("accepted_csv_task_ids_match_state", True, set(accepted["task_id"]) == accepted_ids))
    joined = accepted.merge(registry, on="task_id", suffixes=("", "_registry"))
    identity_mismatch = sum(
        int((joined[field] != joined[f"{field}_registry"]).sum())
        for field in ("symbol", "condition", "repetition")
    )
    checks.append(_check("accepted_csv_identity_matches_registry", 0, identity_mismatch))
    checks.append(_check("failure_summary_task_ids_match_state", True, set(failures["task_id"]) == failed_ids))

    comparison: list[dict[str, Any]] = []
    if compare_dir is not None:
        canonical = run_dir / CANONICAL_ANALYSIS
        for path in sorted(canonical.iterdir()):
            other = compare_dir / path.name
            comparison.append(
                {
                    "file": path.name,
                    "present": other.is_file(),
                    "byte_identical": other.is_file() and other.read_bytes() == path.read_bytes(),
                }
            )
        checks.append(
            _check(
                "regenerated_analysis_byte_identical_files",
                len(comparison),
                sum(1 for row in comparison if row["byte_identical"]),
            )
        )

    accepted_counts = accepted["condition"].value_counts()
    return {
        "run_id": plan["run_id"],
        "plan_sha256": plan["plan_sha256"],
        "effective_max_attempts_per_task": max_attempts,
        "all_checks_passed": all(check["passed"] for check in checks),
        "checks": checks,
        "accepted_by_condition": {c: int(accepted_counts.get(c, 0)) for c in CONDITION_ORDER},
        "failed_tasks": len(failed_ids),
        "failed_securities": int(failures["symbol"].nunique()),
        "accepted_attempt_distribution": {str(k): v for k, v in sorted(accepted_attempt_distribution.items())},
        "validator_replay": dict(replay),
        "regenerated_analysis_comparison": comparison,
    }


def condition_pair_transitions(frame: pd.DataFrame) -> pd.DataFrame:
    score = frame.pivot_table(
        index=["symbol", "repetition"], columns="condition", values="rating_score", aggfunc="first"
    )
    rows: list[dict[str, Any]] = []
    for left, right in CONDITION_PAIRS:
        pair = score[[left, right]].dropna()
        delta = pair[right] - pair[left]
        rows.append(
            {
                "left_condition": left,
                "right_condition": right,
                "comparison": "adjacent" if (left, right) in ADJACENT else "direct",
                "paired_assessments": int(len(pair)),
                "changed": int((delta != 0).sum()),
                "rating_change_pct": float(100 * (delta != 0).mean()),
                "upgrades": int((delta > 0).sum()),
                "downgrades": int((delta < 0).sum()),
                "mean_ordinal_change": float(delta.mean()),
            }
        )
    return pd.DataFrame(rows)


def _complete_symbols(frame: pd.DataFrame, condition: str) -> set[str]:
    sizes = frame.loc[frame["condition"] == condition].groupby("symbol").size()
    return set(sizes[sizes == 3].index)


def bootstrap_specs(frame: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Unit-level values whose ratio of sums defines each statistic."""
    specs: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    score = frame.pivot_table(
        index=["symbol", "repetition"], columns="condition", values="rating_score", aggfunc="first"
    )
    for left, right in CONDITION_PAIRS:
        pair = score[[left, right]].dropna().reset_index()
        delta = pair[right] - pair[left]
        label = f"{CODE[left]}_to_{CODE[right]}"
        specs.append(
            {
                "statistic": f"rating_change_{label}",
                "family": "rating_change",
                "scale": "percent",
                "unit": "security_repetition_pair",
                "units": pd.DataFrame({"symbol": pair["symbol"], "value": (delta != 0).astype(float)}),
            }
        )
        if (left, right) in ADJACENT or label == "A_to_D":
            specs.append(
                {
                    "statistic": f"mean_ordinal_change_{label}",
                    "family": "mean_ordinal_change",
                    "scale": "ordinal_points",
                    "unit": "security_repetition_pair",
                    "units": pd.DataFrame({"symbol": pair["symbol"], "value": delta.astype(float)}),
                }
            )

    cohort = _complete_symbols(frame, "D_CURRENT_WEB")
    for condition in CONDITION_ORDER:
        subset = frame.loc[(frame["condition"] == condition) & frame["symbol"].isin(cohort)]
        groups = subset.groupby("symbol")["rating"]
        unanimous = groups.nunique().eq(1) & groups.size().eq(3)
        specs.append(
            {
                "statistic": f"exact_three_run_unanimity_{CODE[condition]}_matched_complete_d_cohort",
                "family": "exact_three_run_unanimity",
                "scale": "percent",
                "unit": "security",
                "units": pd.DataFrame({"symbol": unanimous.index, "value": unanimous.astype(float).to_numpy()}),
            }
        )
    differences.append(
        {
            "statistic": "exact_three_run_unanimity_D_minus_C_matched_complete_d_cohort",
            "family": "exact_three_run_unanimity",
            "left": "exact_three_run_unanimity_C_matched_complete_d_cohort",
            "right": "exact_three_run_unanimity_D_matched_complete_d_cohort",
        }
    )

    c_d = frame.loc[frame["condition"].isin(["C_QUANTITATIVE", "D_CURRENT_WEB"])]
    for baseline, column in BASELINES.items():
        wide = c_d.pivot(index=["symbol", "repetition"], columns="condition", values=column)
        wide = wide.dropna().reset_index()
        for condition in ("C_QUANTITATIVE", "D_CURRENT_WEB"):
            specs.append(
                {
                    "statistic": f"analyst_exact_match_{baseline}_{CODE[condition]}_matched_c_d",
                    "family": "analyst_exact_match",
                    "scale": "percent",
                    "unit": "security_repetition_pair",
                    "units": pd.DataFrame(
                        {"symbol": wide["symbol"], "value": wide[condition].map(bool).astype(float)}
                    ),
                }
            )
        differences.append(
            {
                "statistic": f"analyst_exact_match_{baseline}_D_minus_C_matched_c_d",
                "family": "analyst_exact_match",
                "left": f"analyst_exact_match_{baseline}_C_matched_c_d",
                "right": f"analyst_exact_match_{baseline}_D_matched_c_d",
            }
        )

    d = frame.loc[frame["condition"] == "D_CURRENT_WEB"]
    specs.append(
        {
            "statistic": "d_reported_analyst_opinions_encountered",
            "family": "analyst_exposure",
            "scale": "percent",
            "unit": "assessment",
            "units": pd.DataFrame(
                {"symbol": d["symbol"], "value": d["analyst_opinions_encountered"].map(bool).astype(float)}
            ),
        }
    )
    return specs, differences


def cluster_bootstrap(
    specs: list[dict[str, Any]],
    differences: list[dict[str, Any]],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> pd.DataFrame:
    """Percentile intervals from resampling whole securities with replacement.

    Every statistic is a ratio of sums over units. One multinomial weight matrix
    is shared by all statistics, so differences use the same resampled
    securities on both sides.
    """
    symbols = sorted(set().union(*(set(spec["units"]["symbol"]) for spec in specs)))
    position = {symbol: index for index, symbol in enumerate(symbols)}
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(
        len(symbols), np.full(len(symbols), 1 / len(symbols)), size=resamples
    ).astype(float)

    def interval(draws: np.ndarray) -> tuple[float, float, float]:
        finite = draws[np.isfinite(draws)]
        low, high = np.percentile(finite, [2.5, 97.5])
        return float(low), float(high), float(finite.std(ddof=1))

    draws: dict[str, np.ndarray] = {}
    rows: list[dict[str, Any]] = []
    for spec in specs:
        units = spec["units"]
        index = units["symbol"].map(position).to_numpy()
        values = units["value"].astype(float).to_numpy()
        numerator = np.bincount(index, weights=values, minlength=len(symbols))
        denominator = np.bincount(index, minlength=len(symbols)).astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            boot = (weights @ numerator) / (weights @ denominator)
        draws[spec["statistic"]] = boot
        scale = 100.0 if spec["scale"] == "percent" else 1.0
        estimate = float(values.sum() / len(values))
        low, high, cluster_se = interval(boot)
        iid_se = float(values.std(ddof=1) / math.sqrt(len(values))) if len(values) > 1 else float("nan")
        rows.append(
            {
                "statistic": spec["statistic"],
                "family": spec["family"],
                "kind": "level",
                "scale": spec["scale"],
                "unit": spec["unit"],
                "units": int(len(values)),
                "securities": int(units["symbol"].nunique()),
                "numerator": float(values.sum()),
                "estimate": scale * estimate,
                "ci95_low": scale * low,
                "ci95_high": scale * high,
                "cluster_bootstrap_se": scale * cluster_se,
                "iid_se": scale * iid_se,
                "design_effect": (cluster_se / iid_se) ** 2 if iid_se > 0 else float("nan"),
                "share_of_resamples_with_opposite_sign": float("nan"),
            }
        )
    estimates = {row["statistic"]: row for row in rows}
    for difference in differences:
        left, right = estimates[difference["left"]], estimates[difference["right"]]
        boot = draws[difference["right"]] - draws[difference["left"]]
        scale = 100.0 if left["scale"] == "percent" else 1.0
        low, high, cluster_se = interval(boot)
        finite = boot[np.isfinite(boot)]
        estimate = right["estimate"] - left["estimate"]
        opposite = finite <= 0 if estimate > 0 else finite >= 0
        rows.append(
            {
                "statistic": difference["statistic"],
                "family": difference["family"],
                "kind": "paired_difference",
                "scale": left["scale"],
                "unit": left["unit"],
                "units": left["units"],
                "securities": left["securities"],
                "numerator": float("nan"),
                "estimate": estimate,
                "ci95_low": scale * low,
                "ci95_high": scale * high,
                "cluster_bootstrap_se": scale * cluster_se,
                "iid_se": float("nan"),
                "design_effect": float("nan"),
                "share_of_resamples_with_opposite_sign": float(np.mean(opposite)),
            }
        )
    result = pd.DataFrame(rows)
    result.insert(len(result.columns), "resamples", resamples)
    result.insert(len(result.columns), "seed", seed)
    return result


def hypergeometric_upper_tail(observed: int, population: int, successes: int, draws: int) -> float:
    """P(X >= observed) when drawing ``draws`` from ``population`` with ``successes``."""
    total = math.comb(population, draws)
    upper = min(draws, successes)
    return float(
        sum(
            math.comb(successes, x) * math.comb(population - successes, draws - x)
            for x in range(max(observed, 0), upper + 1)
        )
        / total
    )


def missingness_by_sector(registry: pd.DataFrame, failures: pd.DataFrame) -> pd.DataFrame:
    securities = registry.drop_duplicates("symbol")[["symbol", "sector"]]
    affected = set(failures["symbol"])
    population, successes = len(securities), len(affected)
    failed_by_sector = failures.merge(securities, on="symbol")["sector"].value_counts()
    rows: list[dict[str, Any]] = []
    for sector, group in securities.groupby("sector"):
        size = len(group)
        hit = int(group["symbol"].isin(affected).sum())
        p_value = hypergeometric_upper_tail(hit, population, successes, size)
        rows.append(
            {
                "sector": sector,
                "securities": size,
                "affected_securities": hit,
                "affected_securities_pct": 100 * hit / size,
                "expected_affected_if_random": size * successes / population,
                "failed_d_tasks": int(failed_by_sector.get(sector, 0)),
                "failed_d_task_pct": 100 * int(failed_by_sector.get(sector, 0)) / (3 * size),
                "hypergeometric_p_at_least_observed": p_value,
                "bonferroni_p": min(1.0, p_value * securities["sector"].nunique()),
            }
        )
    return pd.DataFrame(rows).sort_values("affected_securities", ascending=False).reset_index(drop=True)


def missingness_security_profile(
    frame: pd.DataFrame, failures: pd.DataFrame, *, permutations: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED
) -> pd.DataFrame:
    """Compare securities with an excluded D task against all other securities."""
    c = frame.loc[frame["condition"] == "C_QUANTITATIVE"].groupby("symbol")
    d = frame.loc[frame["condition"] == "D_CURRENT_WEB"].groupby("symbol")
    per_security = pd.DataFrame(
        {
            "c_mean_rating_score": c["rating_score"].mean(),
            "c_three_run_unanimous": c["rating"].nunique().eq(1).astype(float),
            "c_mean_confidence": c["confidence_0_100"].mean(),
            "d_mean_rating_score_accepted_rows": d["rating_score"].mean(),
            "count_normalized_analyst_score": frame.groupby("symbol")["count_normalized_score"].first(),
        }
    )
    affected = per_security.index.isin(set(failures["symbol"]))
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for metric in per_security.columns:
        values = per_security[metric]
        mask = values.notna().to_numpy()
        series = values.to_numpy()[mask]
        labels = affected[mask]
        observed = series[labels].mean() - series[~labels].mean()
        permuted = np.empty(permutations)
        for index in range(permutations):
            shuffled = rng.permutation(labels)
            permuted[index] = series[shuffled].mean() - series[~shuffled].mean()
        rows.append(
            {
                "metric": metric,
                "affected_securities": int(labels.sum()),
                "other_securities": int((~labels).sum()),
                "affected_mean": float(series[labels].mean()),
                "other_mean": float(series[~labels].mean()),
                "difference": float(observed),
                "permutation_p_two_sided": float(np.mean(np.abs(permuted) >= abs(observed) - 1e-12)),
                "permutations": permutations,
            }
        )
    return pd.DataFrame(rows)


def _offending_flags(item: dict[str, Any], cutoff: date) -> list[str]:
    flags: list[str] = []
    observation = _iso_date(item.get("observation_date"))
    publication = _iso_date(item.get("publication_date"))
    if observation is not None and observation > cutoff:
        flags.append("OBSERVATION_AFTER_CUTOFF")
    if publication is not None and publication > cutoff:
        flags.append("PUBLICATION_AFTER_CUTOFF")
    if item.get("source_url") is not None and item.get("origin") not in WEB_EVIDENCE_ORIGINS:
        flags.append("SOURCE_URL_ON_NON_WEB_ORIGIN")
    return flags


def classify_offending_item(item: dict[str, Any], cutoff: date) -> str:
    flags = _offending_flags(item, cutoff)
    if "PUBLICATION_AFTER_CUTOFF" in flags:
        return "POST_CUTOFF_PUBLICATION"
    if "SOURCE_URL_ON_NON_WEB_ORIGIN" in flags:
        return "URL_ON_NON_WEB_ORIGIN"
    if "OBSERVATION_AFTER_CUTOFF" in flags:
        claim = str(item.get("claim") or "")
        if FORWARD_GUIDANCE.search(claim):
            return "FORECAST_OR_GUIDANCE_PERIOD_END"
        if PRE_ANNOUNCED_EVENT.search(claim):
            return "PRE_ANNOUNCED_EVENT_OR_POLICY_DATE"
        return "POST_CUTOFF_OBSERVATION_NEEDS_REVIEW"
    return "NOT_OFFENDING"


def _normalize_url(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/")


def retrieved_urls(body: dict[str, Any]) -> set[str]:
    """URLs the web tool returned or opened during one response."""
    found: set[str] = set()
    for output in body.get("output") or []:
        if not isinstance(output, dict) or output.get("type") != "web_search_call":
            continue
        action = output.get("action") or {}
        if action.get("url"):
            found.add(_normalize_url(str(action["url"])))
        for source in action.get("sources") or []:
            if isinstance(source, dict) and source.get("url"):
                found.add(_normalize_url(str(source["url"])))
    return found


def apply_alternative_date_rule(payload: dict[str, Any], cutoff: date) -> tuple[dict[str, Any], int]:
    """Sensitivity rule only: a dated web item may name a future period endpoint.

    The endpoint is removed from observation_date, as the later v1.4.1 prompt
    instructs, only when the web source's publication_date is on or before the
    cutoff. Every other check, including URL-origin rules, is unchanged.
    """
    modified = copy.deepcopy(payload)
    changed = 0
    for item in modified.get("evidence") or []:
        if not isinstance(item, dict) or item.get("origin") not in WEB_EVIDENCE_ORIGINS:
            continue
        observation = _iso_date(item.get("observation_date"))
        publication = _iso_date(item.get("publication_date"))
        if observation is not None and observation > cutoff and publication is not None and publication <= cutoff:
            item["observation_date"] = None
            changed += 1
    return modified, changed


def audit_failures(
    run_dir: Path, failures: pd.DataFrame, registry: pd.DataFrame, plan: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cutoff = date.fromisoformat(str(plan["as_of_date"]))
    sectors = registry.drop_duplicates("symbol").set_index("symbol")["sector"]
    evidence_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    for failure in failures.itertuples(index=False):
        attempts = list(iter_attempts(run_dir, failure.task_id, int(failure.attempts)))
        rejection_types: list[str] = []
        classes: Counter[str] = Counter()
        publication_dates: list[str] = []
        cited = retrieved = 0
        alternative_attempt: int | None = None
        alternative_payload: dict[str, Any] | None = None
        for item in attempts:
            payload, body = item["payload"], item["body"]
            if payload is None:
                rejection_types.append("NO_STRUCTURED_OUTPUT")
                continue
            error = str(item["checkpoint"].get("error") or "")
            rejection_types.append(
                "FUTURE_OBSERVATION_DATE" if "observation_date" in error and "after the assessment cutoff" in error
                else "INVALID_SOURCE_URL_ORIGIN" if "source_url with a web origin" in error
                else "OTHER"
            )
            urls = retrieved_urls(body)
            for index, evidence in enumerate(payload.get("evidence") or []):
                flags = _offending_flags(evidence, cutoff)
                if not flags:
                    continue
                label = classify_offending_item(evidence, cutoff)
                classes[label] += 1
                url = evidence.get("source_url")
                url_retrieved = bool(url) and _normalize_url(str(url)) in urls
                cited += int(bool(url))
                retrieved += int(url_retrieved)
                if evidence.get("publication_date"):
                    publication_dates.append(str(evidence["publication_date"]))
                evidence_rows.append(
                    {
                        "task_id": failure.task_id,
                        "symbol": failure.symbol,
                        "repetition": int(failure.repetition),
                        "attempt": item["attempt"],
                        "evidence_index": index,
                        "first_rejected_item": f"evidence[{index}]" in error,
                        "flags": "|".join(flags),
                        "audit_class": label,
                        "origin": evidence.get("origin"),
                        "category": evidence.get("category"),
                        "observation_date": evidence.get("observation_date"),
                        "publication_date": evidence.get("publication_date"),
                        "source_url": url,
                        "source_url_returned_by_web_tool": url_retrieved if url else pd.NA,
                        "response_created_at_utc": (
                            pd.Timestamp(body["created_at"], unit="s", tz="UTC").isoformat()
                            if body.get("created_at") else None
                        ),
                        "claim": evidence.get("claim"),
                    }
                )
            if alternative_attempt is None:
                modified, _ = apply_alternative_date_rule(payload, cutoff)
                if replay_validation(modified, symbol=failure.symbol, condition=failure.condition, plan=plan) is None:
                    alternative_attempt, alternative_payload = item["attempt"], modified
        structured = [kind for kind in rejection_types if kind != "NO_STRUCTURED_OUTPUT"]
        task_rows.append(
            {
                "task_id": failure.task_id,
                "symbol": failure.symbol,
                "sector": sectors.get(failure.symbol),
                "repetition": int(failure.repetition),
                "attempts": int(failure.attempts),
                "final_failure_cause": failure.failure_cause,
                "attempt_outcomes": "|".join(rejection_types),
                "structured_attempts": len(structured),
                "same_rejection_type_every_structured_attempt": len(set(structured)) == 1,
                "offending_item_classes": "|".join(f"{k}={v}" for k, v in sorted(classes.items())),
                "any_post_cutoff_publication": classes["POST_CUTOFF_PUBLICATION"] > 0,
                "earliest_offending_publication_date": min(publication_dates) if publication_dates else None,
                "latest_offending_publication_date": max(publication_dates) if publication_dates else None,
                "offending_urls_cited": cited,
                "offending_urls_returned_by_web_tool": retrieved,
                "alternative_rule_first_passing_attempt": alternative_attempt,
                "alternative_rule_rating": (alternative_payload or {}).get("rating"),
            }
        )
    return pd.DataFrame(task_rows), pd.DataFrame(evidence_rows)


def _alternative_records(
    run_dir: Path, state: dict[str, Any], registry: pd.DataFrame, plan: dict[str, Any], *, reselect_all: bool
) -> dict[str, dict[str, Any]]:
    """Payloads that the alternative date rule would select, keyed by task ID."""
    cutoff = date.fromisoformat(str(plan["as_of_date"]))
    lookup = registry.set_index("task_id")
    selected: dict[str, dict[str, Any]] = {}
    for task_id, task in state["tasks"].items():
        meta = lookup.loc[task_id]
        if meta["condition"] != "D_CURRENT_WEB":
            continue
        if not reselect_all and task.get("status") != "FAILED":
            continue
        for item in iter_attempts(run_dir, task_id, int(task.get("attempts") or 0)):
            if item["payload"] is None:
                continue
            modified, _ = apply_alternative_date_rule(item["payload"], cutoff)
            if replay_validation(modified, symbol=str(meta["symbol"]), condition="D_CURRENT_WEB", plan=plan) is None:
                selected[task_id] = {"attempt": item["attempt"], "payload": modified}
                break
    return selected


def _variant_frame(
    accepted: pd.DataFrame, selected: dict[str, dict[str, Any]], registry: pd.DataFrame, consensus: pd.DataFrame
) -> tuple[pd.DataFrame, int, int]:
    lookup = registry.set_index("task_id")
    rows = []
    replaced = added = 0
    accepted_attempts = accepted.set_index("task_id")["attempt"].to_dict()
    for task_id, choice in selected.items():
        if task_id in accepted_attempts:
            if int(accepted_attempts[task_id]) == choice["attempt"]:
                continue
            replaced += 1
        else:
            added += 1
        meta, payload = lookup.loc[task_id], choice["payload"]
        rows.append(
            {
                "task_id": task_id,
                "symbol": meta["symbol"],
                "condition": meta["condition"],
                "repetition": int(meta["repetition"]),
                "rating": payload["rating"],
                "rating_score": RATING_SCORE[payload["rating"]],
                "confidence_0_100": payload["confidence_0_100"],
                "evidence_sufficiency": payload["evidence_sufficiency"],
                "forced_rating_due_to_insufficient_data": bool(payload["forced_rating_due_to_insufficient_data"]),
                "analyst_opinions_encountered": bool(payload["analyst_opinions_encountered"]),
                "analyst_opinions_used": bool(payload["analyst_opinions_used"]),
                "attempt": choice["attempt"],
            }
        )
    changed = attach_analyst_comparison(pd.DataFrame(rows), consensus) if rows else pd.DataFrame()
    keep = accepted.loc[~accepted["task_id"].isin(set(changed.get("task_id", [])))]
    return pd.concat([keep, changed], ignore_index=True), added, replaced


def headline_statistics(frame: pd.DataFrame) -> dict[str, Any]:
    transitions = condition_pair_transitions(frame).set_index(["left_condition", "right_condition"])
    d = frame.loc[frame["condition"] == "D_CURRENT_WEB"]
    cohort = _complete_symbols(frame, "D_CURRENT_WEB")
    result: dict[str, Any] = {"d_accepted": int(len(d)), "complete_d_securities": len(cohort)}
    for left, right in [("C_QUANTITATIVE", "D_CURRENT_WEB"), ("A_MEMORY_ONLY", "D_CURRENT_WEB")]:
        row = transitions.loc[(left, right)]
        label = f"{CODE[left]}_to_{CODE[right]}"
        result[f"rating_change_{label}_pct"] = float(row["rating_change_pct"])
        result[f"rating_change_{label}_pairs"] = int(row["paired_assessments"])
    for condition in ("C_QUANTITATIVE", "D_CURRENT_WEB"):
        groups = frame.loc[(frame["condition"] == condition) & frame["symbol"].isin(cohort)].groupby("symbol")["rating"]
        result[f"exact_three_run_unanimity_{CODE[condition]}_pct"] = float(100 * groups.nunique().eq(1).mean())
    c_d = frame.loc[frame["condition"].isin(["C_QUANTITATIVE", "D_CURRENT_WEB"])]
    for baseline, column in BASELINES.items():
        wide = c_d.pivot(index=["symbol", "repetition"], columns="condition", values=column).dropna()
        result[f"analyst_exact_match_{baseline}_pairs"] = int(len(wide))
        for condition in ("C_QUANTITATIVE", "D_CURRENT_WEB"):
            result[f"analyst_exact_match_{baseline}_{CODE[condition]}_pct"] = float(
                100 * wide[condition].map(bool).mean()
            )
    result["d_mean_rating_score"] = float(d["rating_score"].mean())
    result["d_hold_pct"] = float(100 * d["rating"].eq("HOLD").mean())
    result["d_reported_analyst_opinions_encountered_pct"] = float(
        100 * d["analyst_opinions_encountered"].map(bool).mean()
    )
    return result


def sensitivity_alternative_date_rule(
    run_dir: Path, accepted: pd.DataFrame, registry: pd.DataFrame, plan: dict[str, Any]
) -> pd.DataFrame:
    state = _read_json(run_dir / "state.json")
    consensus = pd.read_csv(run_dir / "frozen_inputs" / "eligible_analyst_consensus.csv")
    rows = [{"variant": "PRIMARY_FROZEN_RULE", "added_d_rows": 0, "replaced_d_rows": 0, **headline_statistics(accepted)}]
    for variant, reselect_all in (
        ("SENSITIVITY_FILL_EXCLUDED_ONLY", False),
        ("SENSITIVITY_RESELECT_ALL_D_ATTEMPTS", True),
    ):
        selected = _alternative_records(run_dir, state, registry, plan, reselect_all=reselect_all)
        frame, added, replaced = _variant_frame(accepted, selected, registry, consensus)
        rows.append({"variant": variant, "added_d_rows": added, "replaced_d_rows": replaced, **headline_statistics(frame)})
    return pd.DataFrame(rows)


def accepted_guidance_dating(frame: pd.DataFrame, failures: pd.DataFrame) -> dict[str, Any]:
    """How accepted D rows dated web management-guidance evidence."""
    d = frame.loc[frame["condition"] == "D_CURRENT_WEB"]
    relation: Counter[str] = Counter()
    rows_with_guidance: set[str] = set()
    for row in d.itertuples(index=False):
        for item in json.loads(row.evidence):
            if item.get("origin") not in WEB_EVIDENCE_ORIGINS or item.get("category") != "MANAGEMENT_GUIDANCE":
                continue
            rows_with_guidance.add(row.task_id)
            observation, publication = item.get("observation_date"), item.get("publication_date")
            if observation is None:
                relation["observation_date_null"] += 1
            elif observation == publication:
                relation["observation_date_equals_publication_date"] += 1
            elif observation < publication:
                relation["observation_date_before_publication_date"] += 1
            else:
                relation["observation_date_after_publication_date_not_after_cutoff"] += 1
    affected = d.loc[d["symbol"].isin(set(failures["symbol"]))]
    return {
        "accepted_d_rows": int(len(d)),
        "accepted_d_rows_with_web_management_guidance_item": len(rows_with_guidance),
        "accepted_d_rows_with_web_management_guidance_item_pct": 100 * len(rows_with_guidance) / len(d),
        "web_management_guidance_items": int(sum(relation.values())),
        "observation_date_relation": dict(relation),
        "affected_securities_accepted_d_rows": int(len(affected)),
        "affected_securities_accepted_d_rows_with_guidance_item": int(affected["task_id"].isin(rows_with_guidance).sum()),
        "note": (
            "Category MANAGEMENT_GUIDANCE only; guidance cited under other categories is not counted. "
            "Accepted rows cite guidance too, so exclusion depended on how the endpoint was dated."
        ),
    }


def failure_audit_summary(tasks: pd.DataFrame, evidence: pd.DataFrame) -> dict[str, Any]:
    return {
        "excluded_tasks": int(len(tasks)),
        "excluded_securities": int(tasks["symbol"].nunique()),
        "final_failure_cause": tasks["final_failure_cause"].value_counts().sort_index().to_dict(),
        "excluded_tasks_by_repetition": {str(k): int(v) for k, v in tasks["repetition"].value_counts().sort_index().items()},
        "offending_items_by_class_all_attempts": evidence["audit_class"].value_counts().sort_index().to_dict(),
        "offending_items_reviewed": int(len(evidence)),
        "tasks_with_any_post_cutoff_publication": int(tasks["any_post_cutoff_publication"].sum()),
        "tasks_same_rejection_type_every_structured_attempt": int(
            tasks["same_rejection_type_every_structured_attempt"].sum()
        ),
        "attempts_without_structured_output": int(tasks["attempt_outcomes"].str.count("NO_STRUCTURED_OUTPUT").sum()),
        "offending_urls_cited": int(tasks["offending_urls_cited"].sum()),
        "offending_urls_returned_by_web_tool": int(tasks["offending_urls_returned_by_web_tool"].sum()),
        "offending_publication_date_range": [
            evidence["publication_date"].dropna().min(),
            evidence["publication_date"].dropna().max(),
        ],
        "response_created_at_utc_range": [
            evidence["response_created_at_utc"].dropna().min(),
            evidence["response_created_at_utc"].dropna().max(),
        ],
        "tasks_passing_alternative_date_rule": int(tasks["alternative_rule_first_passing_attempt"].notna().sum()),
        "note": (
            "Classes are rule-based from evidence fields and claim text. Publication dates are model-reported; "
            "the audit checks that the cited URL was returned by the web tool, not the page's contents."
        ),
    }


def audit(
    run_dir: Path,
    output_dir: Path,
    *,
    analysis_dir: Path | None = None,
    compare_dir: Path | None = None,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    if output_dir.name in PROTECTED_OUTPUTS and output_dir.parent == run_dir:
        raise ValueError(f"Refusing to write audit output into {output_dir.name}")
    analysis_dir = analysis_dir or run_dir / CANONICAL_ANALYSIS
    plan = _read_json(run_dir / "official_plan.json")
    registry = pd.read_json(run_dir / str(plan["task_registry"]), lines=True)
    accepted = load_accepted(analysis_dir)
    failures = pd.read_csv(analysis_dir / "failure_summary.csv")

    reconciliation = reconcile(run_dir, accepted, failures, compare_dir)
    transitions = condition_pair_transitions(accepted)
    specs, differences = bootstrap_specs(accepted)
    intervals = cluster_bootstrap(specs, differences, resamples=resamples, seed=seed)
    sector = missingness_by_sector(registry, failures)
    profile = missingness_security_profile(accepted, failures, permutations=resamples, seed=seed)
    tasks, evidence = audit_failures(run_dir, failures, registry, plan)
    failure_summary = failure_audit_summary(tasks, evidence)
    sensitivity = sensitivity_alternative_date_rule(run_dir, accepted, registry, plan)
    guidance = accepted_guidance_dating(accepted, failures)

    summary = {
        "run_id": plan["run_id"],
        "as_of_date": plan["as_of_date"],
        "source_analysis": analysis_dir.name,
        "reconciliation_all_checks_passed": reconciliation["all_checks_passed"],
        "bootstrap": {
            "method": "Percentile intervals; whole securities resampled with replacement",
            "resamples": resamples,
            "seed": seed,
        },
        "headline_intervals": intervals.set_index("statistic")[["estimate", "ci95_low", "ci95_high", "units"]]
        .round(3)
        .to_dict(orient="index"),
        "failure_audit": failure_summary,
        "sensitivity_variants": sensitivity.set_index("variant").round(3).to_dict(orient="index"),
        "interpretation_guardrails": [
            "The primary dataset and its frozen validation rule are unchanged; sensitivity rows are not accepted results.",
            "Bootstrap intervals reflect security-level sampling variation, not model-version or data-source uncertainty.",
            "Analyst agreement measures similarity, not accuracy; stability measures consistency, not correctness.",
            "Model-reported publication dates were not independently verified against archived page contents.",
        ],
    }

    _atomic_json(output_dir / "reconciliation.json", reconciliation)
    _atomic_csv(output_dir / "condition_pair_transitions.csv", transitions)
    _atomic_csv(output_dir / "bootstrap_intervals.csv", intervals)
    _atomic_csv(output_dir / "missingness_by_sector.csv", sector)
    _atomic_csv(output_dir / "missingness_security_profile.csv", profile)
    _atomic_csv(output_dir / "failure_task_audit.csv", tasks)
    _atomic_csv(output_dir / "failure_evidence_audit.csv", evidence)
    _atomic_json(output_dir / "failure_audit_summary.json", failure_summary)
    _atomic_csv(output_dir / "sensitivity_alternative_date_rule.csv", sensitivity)
    _atomic_json(output_dir / "accepted_d_guidance_dating.json", guidance)
    _atomic_json(output_dir / "audit_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, help="Defaults to the canonical attempt-limited analysis")
    parser.add_argument("--compare-dir", type=Path, help="Regenerated analysis to byte-compare with the canonical one")
    parser.add_argument("--shareable-dir", type=Path, help="Also copy aggregate-only outputs here")
    parser.add_argument("--resamples", type=int, default=DEFAULT_RESAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    summary = audit(
        run_dir,
        output_dir,
        analysis_dir=args.analysis_dir.expanduser().resolve() if args.analysis_dir else None,
        compare_dir=args.compare_dir.expanduser().resolve() if args.compare_dir else None,
        resamples=args.resamples,
        seed=args.seed,
    )
    if args.shareable_dir:
        shareable = args.shareable_dir.expanduser().resolve()
        shareable.mkdir(parents=True, exist_ok=True)
        for name in SHAREABLE_FILES:
            shutil.copy2(output_dir / name, shareable / name)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
