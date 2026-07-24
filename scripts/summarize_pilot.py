#!/usr/bin/env python3
"""Summarize one pilot run without loading environment variables or credentials.

The output is deterministic JSON derived only from an explicit run directory.
The script intentionally reads a small allowlist of artifact names and never
reads .env files.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from itertools import combinations
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Iterable
from urllib.parse import urlparse


SUMMARY_SCHEMA_VERSION = "1.0"
RATING_ORDER = ["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"]
RATING_SCORE = {
    "STRONG_BUY": 2,
    "BUY": 1,
    "HOLD": 0,
    "SELL": -1,
    "STRONG_SELL": -2,
}
SUFFICIENCY_ORDER = ["LOW", "MEDIUM", "HIGH"]
SUFFICIENCY_SCORE = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
DEFAULT_CONDITIONS = [
    "A_MEMORY_ONLY",
    "B_PRICE_HISTORY",
    "C_QUANTITATIVE",
    "D_CURRENT_WEB",
]

ASSESSMENT_CANDIDATES = (
    "assessments.csv",
    "recommendations.csv",
    "analyst_comparison.csv",
)
ASSESSMENT_JSONL_CANDIDATES = ("assessments.jsonl", "recommendations.jsonl")
ANALYST_COMPARISON_CANDIDATES = ("analyst_comparison.csv",)
ANALYST_BASELINE_CANDIDATES = ("analyst_consensus.csv",)
ERROR_CANDIDATES = ("assessment_errors.csv", "errors.csv")

SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|authorization|bearer)\b\s*[:=]?\s*[A-Za-z0-9._-]{8,}"
    ),
)
WEB_ANALYST_TERMS = (
    "analyst",
    "analyst rating",
    "analyst ratings",
    "price target",
    "consensus",
    "upgrade",
    "downgrade",
    "overweight",
    "underweight",
    "tipranks",
    "marketbeat",
    "zacks",
)


def _redact_text(value: Any) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not parse {path.name}: {type(exc).__name__}")
        return {}
    if not isinstance(value, dict):
        warnings.append(f"{path.name} is not a JSON object")
        return {}
    return value


def _read_csv(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                warnings.append(f"{path.name} has no CSV header")
                return []
            return [dict(row) for row in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        warnings.append(f"Could not parse {path.name}: {type(exc).__name__}")
        return []


def _read_jsonl(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    warnings.append(f"{path.name}:{line_number} is not valid JSON")
                    continue
                if not isinstance(value, dict):
                    warnings.append(f"{path.name}:{line_number} is not a JSON object")
                    continue
                nested = value.get("assessment")
                row = {key: item for key, item in value.items() if key != "assessment"}
                if isinstance(nested, dict):
                    for key, item in nested.items():
                        row.setdefault(key, item)
                rows.append(row)
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"Could not parse {path.name}: {type(exc).__name__}")
    return rows


def _first_present(row: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _integer(value: Any) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _number(value: Any) -> float | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _text(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in {"true", "1", "yes", "y"}:
        return True
    if lowered in {"false", "0", "no", "n"}:
        return False
    return None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    text = _text(value)
    if text is None:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _mean(values: Iterable[float], digits: int = 4) -> float | None:
    materialized = list(values)
    return _rounded(fmean(materialized), digits) if materialized else None


def _normalized_rating(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    normalized = text.upper().replace("-", "_").replace(" ", "_")
    return normalized if normalized in RATING_SCORE else text.upper()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    aliases = {
        "symbol": ("symbol", "ticker"),
        "condition": ("condition", "environment", "prompt_condition"),
        "repetition": ("repetition", "rep", "repeat"),
        "rating": ("rating", "ai_rating", "classification"),
        "rating_score": ("rating_score", "ai_rating_score", "ordinal_score"),
        "confidence_0_100": ("confidence_0_100", "confidence"),
        "evidence_sufficiency": ("evidence_sufficiency", "evidence_quality"),
        "count_normalized_score": (
            "count_normalized_score",
            "analyst_score",
            "consensus_score",
        ),
        "count_normalized_rating": (
            "count_normalized_rating",
            "analyst_rating",
            "consensus_rating",
        ),
        "portal_rating_yahoo": (
            "portal_rating_yahoo",
            "portal_rating",
            "yahoo_rating",
        ),
    }
    for canonical, candidates in aliases.items():
        if _text(normalized.get(canonical)) is None:
            value = _first_present(row, candidates)
            if value is not None:
                normalized[canonical] = value
    normalized["symbol"] = (_text(normalized.get("symbol")) or "").upper()
    normalized["condition"] = _text(normalized.get("condition")) or ""
    normalized["repetition"] = _integer(normalized.get("repetition")) or 1
    normalized["rating"] = _normalized_rating(normalized.get("rating"))
    normalized["count_normalized_rating"] = _normalized_rating(
        normalized.get("count_normalized_rating")
    )
    normalized["portal_rating_yahoo"] = _normalized_rating(
        normalized.get("portal_rating_yahoo")
    )
    return normalized


def _best_supplemental_match(
    row: dict[str, Any], supplemental: list[dict[str, Any]]
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1
    for candidate in supplemental:
        if row.get("symbol") and candidate.get("symbol") != row.get("symbol"):
            continue
        score = 1 if row.get("symbol") else 0
        incompatible = False
        for field, weight in (("condition", 4), ("repetition", 2), ("run_id", 1)):
            left = _text(row.get(field))
            right = _text(candidate.get(field))
            if left and right:
                if left != right:
                    incompatible = True
                    break
                score += weight
        if incompatible:
            continue
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _merge_supplemental(
    rows: list[dict[str, Any]], supplemental: list[dict[str, Any]]
) -> None:
    normalized_supplemental = [_normalize_row(row) for row in supplemental]
    for row in rows:
        match = _best_supplemental_match(row, normalized_supplemental)
        if not match:
            continue
        for key, value in match.items():
            if _text(row.get(key)) is None and _text(value) is not None:
                row[key] = value


def _load_assessments(
    run_dir: Path, warnings: list[str], used_artifacts: set[str]
) -> list[dict[str, Any]]:
    for name in ASSESSMENT_CANDIDATES:
        path = run_dir / name
        if not path.is_file():
            continue
        rows = _read_csv(path, warnings)
        if rows:
            used_artifacts.add(name)
            return [_normalize_row(row) for row in rows]
    for name in ASSESSMENT_JSONL_CANDIDATES:
        path = run_dir / name
        if not path.is_file():
            continue
        rows = _read_jsonl(path, warnings)
        if rows:
            used_artifacts.add(name)
            return [_normalize_row(row) for row in rows]
    warnings.append("No non-empty assessment artifact was found")
    return []


def _load_supplemental_analyst_data(
    run_dir: Path,
    rows: list[dict[str, Any]],
    warnings: list[str],
    used_artifacts: set[str],
) -> None:
    for name in ANALYST_COMPARISON_CANDIDATES:
        path = run_dir / name
        if not path.is_file() or name in used_artifacts:
            continue
        supplemental = _read_csv(path, warnings)
        if supplemental:
            used_artifacts.add(name)
            _merge_supplemental(rows, supplemental)
    for name in ANALYST_BASELINE_CANDIDATES:
        path = run_dir / name
        if not path.is_file():
            continue
        supplemental = _read_csv(path, warnings)
        if supplemental:
            used_artifacts.add(name)
            _merge_supplemental(rows, supplemental)


def _condition_order(manifest: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    configured = manifest.get("conditions")
    result = [str(value) for value in configured] if isinstance(configured, list) else []
    observed = sorted({row.get("condition", "") for row in rows if row.get("condition")})
    for condition in DEFAULT_CONDITIONS + observed:
        if condition in observed and condition not in result:
            result.append(condition)
    return result


def _raw_key_from_name(name: str, conditions: list[str]) -> tuple[str, str, int] | None:
    stem = name.removesuffix(".json")
    for condition in sorted(conditions, key=len, reverse=True):
        marker = f"_{condition}_r"
        if marker not in stem:
            continue
        symbol, repetition = stem.rsplit(marker, 1)
        parsed_repetition = _integer(repetition)
        if symbol and parsed_repetition is not None:
            return symbol.upper(), condition, parsed_repetition
    return None


def _source_domain(url: Any) -> str | None:
    text = _text(url)
    if text is None:
        return None
    try:
        return urlparse(text).hostname
    except ValueError:
        return None


def _safe_source_identity(url: Any) -> str | None:
    """Return a query-free URL identity suitable only for counting."""

    text = _text(url)
    if text is None:
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    scheme = parsed.scheme.lower() if parsed.scheme else "https"
    path = parsed.path or "/"
    return f"{scheme}://{parsed.hostname.lower()}{path}"


def _load_raw_metrics(
    run_dir: Path, conditions: list[str], warnings: list[str], used_artifacts: set[str]
) -> dict[tuple[str, str, int], dict[str, Any]]:
    raw_dir = run_dir / "raw_responses"
    if not raw_dir.is_dir():
        return {}
    metrics: dict[tuple[str, str, int], dict[str, Any]] = {}
    parsed_files = 0
    for path in sorted(raw_dir.glob("*.json")):
        if path.name.startswith(".") or path.name.endswith(".error.json"):
            continue
        key = _raw_key_from_name(path.name, conditions)
        if key is None:
            continue
        raw = _read_json(path, warnings)
        if not raw:
            continue
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        web_calls = [
            item
            for item in raw.get("output", [])
            if isinstance(item, dict) and item.get("type") == "web_search_call"
        ]
        source_references = 0
        source_domains: set[str] = set()
        source_urls: set[str] = set()
        actions: list[dict[str, Any]] = []
        for call in web_calls:
            action = call.get("action") if isinstance(call.get("action"), dict) else {}
            actions.append(action)
            sources = action.get("sources") if isinstance(action.get("sources"), list) else []
            source_references += len(sources)
            for source in sources:
                if not isinstance(source, dict):
                    continue
                domain = _source_domain(source.get("url"))
                if domain:
                    source_domains.add(domain)
                source_identity = _safe_source_identity(source.get("url"))
                if source_identity:
                    source_urls.add(source_identity)
            domain = _source_domain(action.get("url"))
            if domain:
                source_domains.add(domain)
            source_identity = _safe_source_identity(action.get("url"))
            if source_identity:
                source_urls.add(source_identity)
        action_text = json.dumps(actions, sort_keys=True).lower()
        matched_terms = [
            term
            for term in WEB_ANALYST_TERMS
            if re.search(rf"\b{re.escape(term)}\b", action_text)
        ]
        metrics[key] = {
            "input_tokens": _integer(usage.get("input_tokens")),
            "output_tokens": _integer(usage.get("output_tokens")),
            "total_tokens": _integer(usage.get("total_tokens")),
            "web_search_call_count": len(web_calls),
            "web_search_source_count": source_references,
            "analyst_terms_detected_in_web_actions": bool(matched_terms),
            "analyst_web_action_matched_terms": matched_terms,
            "_source_domains": source_domains,
            "_source_urls": source_urls,
        }
        parsed_files += 1
    if parsed_files:
        used_artifacts.add(f"raw_responses/*.json ({parsed_files} files)")
    return metrics


def _enrich_rows_with_raw(
    rows: list[dict[str, Any]],
    raw_metrics: dict[tuple[str, str, int], dict[str, Any]],
) -> None:
    for row in rows:
        key = (
            str(row.get("symbol") or "").upper(),
            str(row.get("condition") or ""),
            int(row.get("repetition") or 1),
        )
        raw = raw_metrics.get(key)
        if not raw:
            continue
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            if _text(row.get(field)) is None:
                row[field] = raw.get(field)
        for field in (
            "web_search_call_count",
            "web_search_source_count",
            "analyst_terms_detected_in_web_actions",
            "analyst_web_action_matched_terms",
        ):
            row[field] = raw.get(field)
        domains = set(raw.get("_source_domains") or set())
        source_urls = set(raw.get("_source_urls") or set())
        for url in _json_list(row.get("retrieved_source_urls")):
            domain = _source_domain(url)
            if domain:
                domains.add(domain)
            source_identity = _safe_source_identity(url)
            if source_identity:
                source_urls.add(source_identity)
        row["_source_domains"] = domains
        row["_source_urls"] = source_urls


def _recompute_final_output_exposure(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("condition") != "D_CURRENT_WEB":
            row["analyst_terms_in_final_output"] = "NOT_APPLICABLE_NO_WEB"
            continue
        output_text = json.dumps(
            {
                "evidence": row.get("evidence"),
                "thesis": row.get("thesis"),
                "risks": row.get("risks"),
            },
            sort_keys=True,
            default=str,
        ).lower()
        detected = any(
            re.search(rf"\b{re.escape(term)}\b", output_text)
            for term in WEB_ANALYST_TERMS
        )
        row["analyst_terms_in_final_output"] = (
            "DETECTED_IN_MODEL_OUTPUT_EVIDENCE"
            if detected
            else "NOT_DETECTED_IN_MODEL_OUTPUT_EVIDENCE"
        )


def _rating_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row.get("rating") for row in rows if row.get("rating"))
    result = {rating: counts.get(rating, 0) for rating in RATING_ORDER}
    for rating in sorted(set(counts) - set(RATING_ORDER)):
        result[str(rating)] = counts[rating]
    return result


def _ratings_summary(
    rows: list[dict[str, Any]], conditions: list[str]
) -> dict[str, Any]:
    condition_rank = {value: index for index, value in enumerate(conditions)}
    observations = []
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("symbol") or ""),
            condition_rank.get(str(item.get("condition") or ""), len(condition_rank)),
            int(item.get("repetition") or 1),
        ),
    ):
        observations.append(
            {
                "symbol": row.get("symbol"),
                "condition": row.get("condition"),
                "repetition": int(row.get("repetition") or 1),
                "rating": row.get("rating"),
                "confidence_0_100": _number(row.get("confidence_0_100")),
                "evidence_sufficiency": _text(row.get("evidence_sufficiency")),
                "forced_rating_due_to_insufficient_data": _boolean(
                    row.get("forced_rating_due_to_insufficient_data")
                ),
            }
        )
    return {
        "by_stock_condition_repetition": observations,
        "distribution_overall": _rating_distribution(rows),
        "distribution_by_condition": {
            condition: _rating_distribution(
                [row for row in rows if row.get("condition") == condition]
            )
            for condition in conditions
        },
    }


def _quality_summary(
    rows: list[dict[str, Any]], conditions: list[str]
) -> dict[str, Any]:
    confidence_values = [
        value
        for row in rows
        if (value := _number(row.get("confidence_0_100"))) is not None
    ]
    sufficiency_counts = Counter(
        _text(row.get("evidence_sufficiency"))
        for row in rows
        if _text(row.get("evidence_sufficiency"))
    )
    sufficiency_values = [
        SUFFICIENCY_SCORE[value]
        for row in rows
        if (value := _text(row.get("evidence_sufficiency"))) in SUFFICIENCY_SCORE
    ]
    forced_values = [
        value
        for row in rows
        if (value := _boolean(row.get("forced_rating_due_to_insufficient_data")))
        is not None
    ]
    return {
        "mean_confidence_overall": _mean(confidence_values),
        "mean_confidence_by_condition": {
            condition: _mean(
                value
                for row in rows
                if row.get("condition") == condition
                if (value := _number(row.get("confidence_0_100"))) is not None
            )
            for condition in conditions
        },
        "evidence_sufficiency_score_mapping": SUFFICIENCY_SCORE,
        "mean_evidence_sufficiency_score_overall": _mean(sufficiency_values),
        "mean_evidence_sufficiency_score_by_condition": {
            condition: _mean(
                SUFFICIENCY_SCORE[value]
                for row in rows
                if row.get("condition") == condition
                if (value := _text(row.get("evidence_sufficiency")))
                in SUFFICIENCY_SCORE
            )
            for condition in conditions
        },
        "evidence_sufficiency_distribution": {
            level: sufficiency_counts.get(level, 0) for level in SUFFICIENCY_ORDER
        },
        "forced_rating_count": sum(forced_values),
        "forced_rating_rate": _rounded(sum(forced_values) / len(forced_values), 4)
        if forced_values
        else None,
    }


def _stability_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("symbol") and row.get("condition"):
            groups[(row["symbol"], row["condition"])].append(row)
    details: list[dict[str, Any]] = []
    repeat_mode_agreements: list[float] = []
    repeat_standard_deviations: list[float] = []
    repeat_unanimous: list[bool] = []
    for (symbol, condition), group in sorted(groups.items()):
        ratings = [row.get("rating") for row in group if row.get("rating")]
        counts = Counter(ratings)
        max_count = max(counts.values()) if counts else 0
        mode_ratings = [
            rating
            for rating in RATING_ORDER
            if counts.get(rating, 0) == max_count and max_count
        ]
        mode_ratings.extend(
            sorted(
                rating
                for rating, count in counts.items()
                if rating not in RATING_ORDER and count == max_count
            )
        )
        scores = [RATING_SCORE[rating] for rating in ratings if rating in RATING_SCORE]
        has_repeats = len(group) >= 2
        mode_agreement = max_count / len(ratings) if ratings else None
        standard_deviation = pstdev(scores) if len(scores) >= 2 else None
        unanimous = len(counts) == 1 if has_repeats and counts else None
        if has_repeats and mode_agreement is not None:
            repeat_mode_agreements.append(mode_agreement)
        if has_repeats and standard_deviation is not None:
            repeat_standard_deviations.append(standard_deviation)
        if unanimous is not None:
            repeat_unanimous.append(unanimous)
        details.append(
            {
                "symbol": symbol,
                "condition": condition,
                "repetitions": len(group),
                "rating_counts": {
                    rating: counts[rating]
                    for rating in RATING_ORDER
                    if counts.get(rating, 0)
                },
                "mode_ratings": mode_ratings,
                "mode_agreement_rate": _rounded(mode_agreement),
                "rating_score_population_sd": _rounded(standard_deviation),
                "unanimous": unanimous,
            }
        )
    return {
        "groups_with_repeated_assessments": len(repeat_mode_agreements),
        "unanimous_group_rate": _rounded(
            sum(repeat_unanimous) / len(repeat_unanimous)
        )
        if repeat_unanimous
        else None,
        "mean_mode_agreement_rate": _mean(repeat_mode_agreements),
        "mean_rating_score_population_sd": _mean(repeat_standard_deviations),
        "by_stock_condition": details,
    }


def _transition_summary(
    rows: list[dict[str, Any]],
    conditions: list[str],
    run_dir: Path,
    warnings: list[str],
    used_artifacts: set[str],
) -> list[dict[str, Any]]:
    lookup: dict[tuple[str, int, str], dict[str, Any]] = {}
    duplicate_keys = 0
    for row in rows:
        key = (
            str(row.get("symbol") or ""),
            int(row.get("repetition") or 1),
            str(row.get("condition") or ""),
        )
        if key in lookup:
            duplicate_keys += 1
            continue
        lookup[key] = row
    if duplicate_keys:
        warnings.append(
            f"Ignored {duplicate_keys} duplicate symbol/repetition/condition assessment rows"
        )

    results: list[dict[str, Any]] = []
    for left, right in combinations(conditions, 2):
        transitions: Counter[str] = Counter()
        signed_changes: list[float] = []
        absolute_changes: list[float] = []
        for symbol, repetition, condition in sorted(lookup):
            if condition != left:
                continue
            left_row = lookup[(symbol, repetition, left)]
            right_row = lookup.get((symbol, repetition, right))
            if not right_row:
                continue
            left_rating = left_row.get("rating")
            right_rating = right_row.get("rating")
            transitions[f"{left_rating}->{right_rating}"] += 1
            if left_rating in RATING_SCORE and right_rating in RATING_SCORE:
                delta = RATING_SCORE[right_rating] - RATING_SCORE[left_rating]
                signed_changes.append(float(delta))
                absolute_changes.append(float(abs(delta)))
        if transitions:
            observation_count = sum(transitions.values())
            changed_count = sum(
                count
                for transition, count in transitions.items()
                if transition.split("->", 1)[0] != transition.split("->", 1)[1]
            )
            results.append(
                {
                    "from_condition": left,
                    "to_condition": right,
                    "observations": observation_count,
                    "changed": changed_count,
                    "flip_rate": _rounded(changed_count / observation_count),
                    "mean_signed_ordinal_change": _mean(signed_changes),
                    "mean_absolute_ordinal_change": _mean(absolute_changes),
                    "rating_transitions": dict(sorted(transitions.items())),
                }
            )
    if results:
        return results

    fallback = run_dir / "condition_changes.csv"
    if not fallback.is_file():
        return []
    fallback_rows = _read_csv(fallback, warnings)
    if fallback_rows:
        used_artifacts.add(fallback.name)
    return [
        {
            "from_condition": _text(row.get("left_condition")),
            "to_condition": _text(row.get("right_condition")),
            "observations": _integer(row.get("observations")),
            "changed": None,
            "flip_rate": _number(row.get("flip_rate")),
            "mean_signed_ordinal_change": None,
            "mean_absolute_ordinal_change": _number(
                row.get("mean_absolute_ordinal_change")
            ),
            "rating_transitions": {},
        }
        for row in fallback_rows
    ]


def _analyst_baseline_value(
    rows: list[dict[str, Any]], symbol: str, field: str
) -> Any:
    values = [
        row.get(field)
        for row in rows
        if row.get("symbol") == symbol and _text(row.get(field)) is not None
    ]
    return values[0] if values else None


def _analyst_match_metrics(
    rows: list[dict[str, Any]], baseline_field: str, conditions: list[str]
) -> dict[str, Any]:
    def compute(group: list[dict[str, Any]]) -> dict[str, Any]:
        eligible = [
            row
            for row in group
            if row.get("rating") in RATING_SCORE
            and row.get(baseline_field) in RATING_SCORE
        ]
        exact = sum(row["rating"] == row[baseline_field] for row in eligible)
        gaps = [
            RATING_SCORE[row["rating"]] - RATING_SCORE[row[baseline_field]]
            for row in eligible
        ]
        return {
            "eligible_assessments": len(eligible),
            "exact_matches": exact,
            "exact_match_rate": _rounded(exact / len(eligible)) if eligible else None,
            "mean_signed_ordinal_gap_ai_minus_analyst": _mean(gaps),
            "mean_absolute_ordinal_gap": _mean(abs(value) for value in gaps),
        }

    return {
        "overall": compute(rows),
        "by_condition": {
            condition: compute(
                [row for row in rows if row.get("condition") == condition]
            )
            for condition in conditions
        },
    }


def _analyst_summary(
    rows: list[dict[str, Any]], conditions: list[str]
) -> dict[str, Any]:
    symbols = sorted({row.get("symbol") for row in rows if row.get("symbol")})
    baselines = []
    for symbol in symbols:
        baselines.append(
            {
                "symbol": symbol,
                "count_normalized_rating": _normalized_rating(
                    _analyst_baseline_value(
                        rows, symbol, "count_normalized_rating"
                    )
                ),
                "count_normalized_score": _number(
                    _analyst_baseline_value(rows, symbol, "count_normalized_score")
                ),
                "count_distribution_analyst_count": _integer(
                    _first_present(
                        {
                            "analyst_count": _analyst_baseline_value(
                                rows, symbol, "analyst_count"
                            )
                        },
                        ("analyst_count",),
                    )
                ),
                "portal_rating_yahoo": _normalized_rating(
                    _analyst_baseline_value(rows, symbol, "portal_rating_yahoo")
                ),
                "portal_analyst_count_yahoo": _integer(
                    _analyst_baseline_value(
                        rows, symbol, "portal_analyst_count_yahoo"
                    )
                ),
            }
        )
    return {
        "baselines_by_symbol": baselines,
        "count_normalized_baseline": _analyst_match_metrics(
            rows, "count_normalized_rating", conditions
        ),
        "portal_yahoo_baseline": _analyst_match_metrics(
            rows, "portal_rating_yahoo", conditions
        ),
        "interpretation": (
            "The two analyst baselines may differ and are reported separately; "
            "neither is treated as ground truth."
        ),
    }


def _analyst_exposure_summary(
    rows: list[dict[str, Any]], conditions: list[str]
) -> dict[str, Any]:
    def metrics(group: list[dict[str, Any]]) -> dict[str, Any]:
        encountered = [
            value
            for row in group
            if (value := _boolean(row.get("analyst_opinions_encountered")))
            is not None
        ]
        used = [
            value
            for row in group
            if (value := _boolean(row.get("analyst_opinions_used"))) is not None
        ]
        web_detected = [
            value
            for row in group
            if (
                value := _boolean(
                    row.get("analyst_terms_detected_in_web_actions")
                )
            )
            is not None
        ]
        final_statuses = Counter(
            _text(
                _first_present(
                    row,
                    (
                        "analyst_terms_in_final_output",
                        "analyst_exposure_audit",
                    ),
                )
            )
            for row in group
            if _text(
                _first_present(
                    row,
                    (
                        "analyst_terms_in_final_output",
                        "analyst_exposure_audit",
                    ),
                )
            )
        )
        return {
            "assessments": len(group),
            "model_reported_encountered": sum(encountered),
            "model_reported_used": sum(used),
            "web_action_term_detection": sum(web_detected),
            "final_output_audit_status_counts": dict(sorted(final_statuses.items())),
        }

    matched_terms = sorted(
        {
            str(term)
            for row in rows
            for term in _json_list(row.get("analyst_web_action_matched_terms"))
        }
    )
    discrepancies = []
    for row in rows:
        encountered = _boolean(row.get("analyst_opinions_encountered")) or False
        used = _boolean(row.get("analyst_opinions_used")) or False
        web_detected = (
            _boolean(row.get("analyst_terms_detected_in_web_actions")) or False
        )
        final_status = _text(
            _first_present(
                row,
                ("analyst_terms_in_final_output", "analyst_exposure_audit"),
            )
        )
        reasons = []
        if web_detected and not encountered:
            reasons.append("WEB_ACTION_TERMS_DETECTED_BUT_NOT_MODEL_REPORTED")
        if used and not encountered:
            reasons.append("MODEL_REPORTED_USE_WITHOUT_ENCOUNTER")
        if final_status and final_status.startswith("DETECTED") and not encountered:
            reasons.append("FINAL_OUTPUT_TERMS_DETECTED_BUT_NOT_MODEL_REPORTED")
        if reasons:
            discrepancies.append(
                {
                    "symbol": row.get("symbol"),
                    "condition": row.get("condition"),
                    "repetition": int(row.get("repetition") or 1),
                    "reasons": reasons,
                }
            )
    return {
        "overall": metrics(rows),
        "by_condition": {
            condition: metrics(
                [row for row in rows if row.get("condition") == condition]
            )
            for condition in conditions
        },
        "web_action_matched_terms": matched_terms,
        "audit_discrepancies": discrepancies,
    }


def _web_summary(
    rows: list[dict[str, Any]], conditions: list[str]
) -> dict[str, Any]:
    def metrics(group: list[dict[str, Any]]) -> dict[str, Any]:
        calls = sum(
            value
            for row in group
            if (value := _integer(row.get("web_search_call_count"))) is not None
        )
        source_references = sum(
            value
            for row in group
            if (value := _integer(row.get("web_search_source_count"))) is not None
        )
        domains: set[str] = set()
        source_urls: set[str] = set()
        for row in group:
            domains.update(row.get("_source_domains") or set())
            source_urls.update(row.get("_source_urls") or set())
            for url in _json_list(row.get("retrieved_source_urls")):
                domain = _source_domain(url)
                if domain:
                    domains.add(domain)
                source_identity = _safe_source_identity(url)
                if source_identity:
                    source_urls.add(source_identity)
        return {
            "web_search_calls": calls,
            "web_source_references": source_references,
            "unique_source_urls": len(source_urls),
            "unique_source_domains": len(domains),
            "source_domains": sorted(domains),
        }

    return {
        "overall": metrics(rows),
        "by_condition": {
            condition: metrics(
                [row for row in rows if row.get("condition") == condition]
            )
            for condition in conditions
        },
    }


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _failed_usage_from_rows(
    rows: list[dict[str, Any]],
    expected_failed_calls: int,
) -> dict[str, int]:
    complete_calls = 0
    any_usage_calls = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    web_search_calls = 0
    for row in rows:
        input_value = _integer(row.get("input_tokens"))
        output_value = _integer(row.get("output_tokens"))
        total_value = _integer(row.get("total_tokens"))
        web_value = _integer(row.get("web_search_call_count"))
        if any(
            value is not None
            for value in (input_value, output_value, total_value, web_value)
        ):
            any_usage_calls += 1
        if input_value is not None and output_value is not None and web_value is not None:
            complete_calls += 1
        input_tokens += input_value or 0
        output_tokens += output_value or 0
        total_tokens += (
            total_value
            if total_value is not None
            else (input_value or 0) + (output_value or 0)
        )
        web_search_calls += web_value or 0
    failed_calls = max(len(rows), expected_failed_calls)
    return {
        "failed_calls": failed_calls,
        "calls_with_any_usage": any_usage_calls,
        "calls_with_usage": complete_calls,
        "unknown_paid_failure_usage_count": max(
            0, failed_calls - complete_calls
        ),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "web_search_calls": web_search_calls,
    }


def _failed_usage_for_summary(
    manifest: dict[str, Any],
    error_rows: list[dict[str, Any]],
    raw_error_files: int,
) -> tuple[dict[str, int], str]:
    manifest_usage = manifest.get("failed_call_usage")
    if isinstance(manifest_usage, dict):
        failed_calls = _integer(manifest_usage.get("failed_calls")) or 0
        calls_with_usage = _integer(
            manifest_usage.get("calls_with_usage")
        ) or 0
        explicit_unknown = _integer(
            manifest.get("unknown_paid_failure_usage_count")
        )
        computed_unknown = max(0, failed_calls - calls_with_usage)
        unknown = (
            computed_unknown
            if explicit_unknown is None
            else max(explicit_unknown, computed_unknown)
        )
        failed_calls = max(failed_calls, calls_with_usage + unknown)
        result = {
            "failed_calls": failed_calls,
            "calls_with_any_usage": (
                _integer(manifest_usage.get("calls_with_any_usage")) or 0
            ),
            "calls_with_usage": calls_with_usage,
            "unknown_paid_failure_usage_count": unknown,
            "input_tokens": _integer(
                manifest_usage.get("input_tokens")
            ) or 0,
            "output_tokens": _integer(
                manifest_usage.get("output_tokens")
            ) or 0,
            "total_tokens": _integer(
                manifest_usage.get("total_tokens")
            ) or 0,
            "web_search_calls": _integer(
                manifest_usage.get("web_search_calls")
            ) or 0,
        }
        return result, "manifest.failed_call_usage"

    failed_attempts = max(
        _integer(manifest.get("failed_assessments")) or 0,
        len(error_rows),
        raw_error_files,
    )
    result = _failed_usage_from_rows(error_rows, failed_attempts)
    explicit_unknown = _integer(
        manifest.get("unknown_paid_failure_usage_count")
    )
    if explicit_unknown is not None:
        result["unknown_paid_failure_usage_count"] = max(
            result["unknown_paid_failure_usage_count"],
            explicit_unknown,
        )
        result["failed_calls"] = max(
            result["failed_calls"],
            result["calls_with_usage"] + explicit_unknown,
        )
    return result, "assessment_errors.csv"


def _usage_and_cost_summary(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    error_rows: list[dict[str, Any]],
    raw_error_files: int,
    input_rate: Decimal,
    output_rate: Decimal,
    web_rate: Decimal,
) -> tuple[dict[str, Any], dict[str, Any]]:
    backend = str(manifest.get("backend") or "").lower()
    completed = _integer(manifest.get("completed_assessments"))
    expected_rows = completed if completed is not None else len(rows)

    input_values = [_integer(row.get("input_tokens")) for row in rows]
    output_values = [_integer(row.get("output_tokens")) for row in rows]
    total_values = [_integer(row.get("total_tokens")) for row in rows]
    web_values = [_integer(row.get("web_search_call_count")) for row in rows]

    if backend in {"dry-run", "dry_run", "dryrun"}:
        input_values = [value if value is not None else 0 for value in input_values]
        output_values = [value if value is not None else 0 for value in output_values]
        total_values = [value if value is not None else 0 for value in total_values]
        web_values = [value if value is not None else 0 for value in web_values]

    successful_input_tokens = sum(
        value for value in input_values if value is not None
    )
    successful_output_tokens = sum(
        value for value in output_values if value is not None
    )
    successful_total_tokens = sum(
        value for value in total_values if value is not None
    )
    if not any(value is not None for value in total_values):
        successful_total_tokens = (
            successful_input_tokens + successful_output_tokens
        )
    successful_web_calls = sum(
        value for value in web_values if value is not None
    )

    usage_complete = (
        len(rows) >= expected_rows
        and sum(value is not None for value in input_values) >= expected_rows
        and sum(value is not None for value in output_values) >= expected_rows
        and sum(value is not None for value in web_values) >= expected_rows
    )
    failed_usage, failed_usage_source = _failed_usage_for_summary(
        manifest,
        error_rows,
        raw_error_files,
    )
    if backend in {"dry-run", "dry_run", "dryrun"}:
        failed_usage = _failed_usage_from_rows([], 0)
        failed_usage_source = "dry-run"
    unknown_paid_failure_usage = failed_usage[
        "unknown_paid_failure_usage_count"
    ]
    input_tokens = successful_input_tokens + failed_usage["input_tokens"]
    output_tokens = successful_output_tokens + failed_usage["output_tokens"]
    total_tokens = successful_total_tokens + failed_usage["total_tokens"]
    web_calls = successful_web_calls + failed_usage["web_search_calls"]
    cost_complete = usage_complete and unknown_paid_failure_usage == 0
    input_cost = Decimal(input_tokens) * input_rate / Decimal(1_000_000)
    output_cost = Decimal(output_tokens) * output_rate / Decimal(1_000_000)
    web_cost = Decimal(web_calls) * web_rate
    successful_cost = (
        Decimal(successful_input_tokens)
        * input_rate
        / Decimal(1_000_000)
        + Decimal(successful_output_tokens)
        * output_rate
        / Decimal(1_000_000)
        + Decimal(successful_web_calls) * web_rate
    )
    failed_cost = (
        Decimal(failed_usage["input_tokens"])
        * input_rate
        / Decimal(1_000_000)
        + Decimal(failed_usage["output_tokens"])
        * output_rate
        / Decimal(1_000_000)
        + Decimal(failed_usage["web_search_calls"]) * web_rate
    )
    known_total = input_cost + output_cost + web_cost
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "web_search_calls": web_calls,
        "assessment_rows_with_input_usage": sum(
            value is not None for value in input_values
        ),
        "assessment_rows_with_output_usage": sum(
            value is not None for value in output_values
        ),
        "successful_assessment_usage": {
            "assessment_rows": len(rows),
            "input_tokens": successful_input_tokens,
            "output_tokens": successful_output_tokens,
            "total_tokens": successful_total_tokens,
            "web_search_calls": successful_web_calls,
        },
        "known_failed_call_usage": {
            **failed_usage,
            "source": failed_usage_source,
        },
        "usage_complete_for_completed_assessments": usage_complete,
        "unknown_paid_failure_usage_count": unknown_paid_failure_usage,
        "usage_complete_for_all_paid_attempts": cost_complete,
    }
    cost = {
        "currency": "USD",
        "assumptions": {
            "input_usd_per_million_tokens": _decimal_text(input_rate),
            "output_usd_per_million_tokens": _decimal_text(output_rate),
            "usd_per_web_search_call": _decimal_text(web_rate),
        },
        "input_cost_usd": _decimal_text(input_cost),
        "output_cost_usd": _decimal_text(output_cost),
        "web_search_cost_usd": _decimal_text(web_cost),
        "known_successful_attempt_cost_usd": _decimal_text(
            successful_cost
        ),
        "known_failed_attempt_cost_usd": _decimal_text(failed_cost),
        "known_total_cost_usd": _decimal_text(known_total),
        "lower_bound_total_cost_usd": _decimal_text(known_total),
        "exact_estimated_total_cost_usd": (
            _decimal_text(known_total) if cost_complete else None
        ),
        "complete": cost_complete,
        "status": (
            "EXACT_FOR_RECORDED_ATTEMPTS"
            if cost_complete
            else "LOWER_BOUND_ONLY_MISSING_USAGE"
        ),
        "unknown_paid_failure_usage_count": unknown_paid_failure_usage,
    }
    return usage, cost


def _load_errors(
    run_dir: Path, warnings: list[str], used_artifacts: set[str]
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    for name in ERROR_CANDIDATES:
        path = run_dir / name
        if not path.is_file():
            continue
        parsed = _read_csv(path, warnings)
        used_artifacts.add(name)
        rows.extend(parsed)
    raw_error_dir = run_dir / "raw_responses"
    raw_error_files = (
        len(list(raw_error_dir.glob("*.error.json")))
        if raw_error_dir.is_dir()
        else 0
    )
    return rows, raw_error_files


def _error_summary(
    rows: list[dict[str, Any]], raw_error_files: int, manifest: dict[str, Any]
) -> dict[str, Any]:
    by_condition = Counter(
        _text(row.get("condition")) or "UNKNOWN" for row in rows
    )
    error_types = Counter()
    for row in rows:
        raw = _redact_text(row.get("error"))
        match = re.match(r"([A-Za-z_][A-Za-z0-9_.]*)\s*:", raw)
        error_types[match.group(1) if match else "UNCLASSIFIED"] += 1
    return {
        "manifest_failed_assessments": _integer(
            manifest.get("failed_assessments")
        ),
        "error_table_rows": len(rows),
        "raw_error_files": raw_error_files,
        "by_condition": dict(sorted(by_condition.items())),
        "error_types": dict(sorted(error_types.items())),
        "messages_omitted_to_prevent_secret_exposure": True,
    }


def _acquisition_summary(
    acquisition: dict[str, Any] | None,
) -> dict[str, Any]:
    if not acquisition:
        return {
            "status": None,
            "strict_for_this_run": None,
            "issue_count": None,
            "issues": [],
        }
    issues = acquisition.get("issues")
    safe_issues = []
    if isinstance(issues, list):
        for issue in issues:
            if isinstance(issue, dict):
                safe_issues.append(
                    {
                        "symbol": _text(issue.get("symbol")),
                        "issue": _redact_text(issue.get("issue")),
                    }
                )
            else:
                safe_issues.append(
                    {"symbol": None, "issue": _redact_text(issue)}
                )
    return {
        "status": _text(acquisition.get("status")),
        "strict_for_this_run": _boolean(
            acquisition.get("strict_for_this_run")
        ),
        "issue_count": len(safe_issues),
        "issues": safe_issues,
    }


def _completion_summary(
    manifest: dict[str, Any],
    assessment_rows: int,
    error_rows: int,
    raw_error_files: int,
) -> dict[str, Any]:
    planned = _integer(manifest.get("planned_assessments"))
    completed = _integer(manifest.get("completed_assessments"))
    failed = _integer(manifest.get("failed_assessments"))
    not_attempted = _integer(manifest.get("not_attempted_assessments"))
    completed = assessment_rows if completed is None else completed
    failed = max(error_rows, raw_error_files) if failed is None else failed
    if planned is not None and not_attempted is None:
        not_attempted = max(0, planned - completed - failed)
    consistency_issues = []
    if completed != assessment_rows:
        consistency_issues.append(
            f"Manifest completed={completed}, assessment rows={assessment_rows}"
        )
    if failed < error_rows:
        consistency_issues.append(
            f"Manifest failed={failed}, error table rows={error_rows}"
        )
    if planned is None:
        run_status = "PLANNED_COUNT_UNKNOWN"
    elif completed + failed + (not_attempted or 0) < planned:
        run_status = "IN_PROGRESS_OR_INCOMPLETE"
    elif failed:
        run_status = "COMPLETE_WITH_FAILURES"
    else:
        run_status = "COMPLETE"
    return {
        "run_status": run_status,
        "planned_assessments": planned,
        "completed_assessments": completed,
        "failed_assessments": failed,
        "not_attempted_assessments": not_attempted,
        "completion_rate": (
            _rounded(completed / planned)
            if planned is not None and planned > 0
            else None
        ),
        "artifact_assessment_rows": assessment_rows,
        "consistency_issues": consistency_issues,
        "aborted_after_failure_threshold": _boolean(
            manifest.get("aborted_after_failure_threshold")
        ),
    }


def build_summary(
    run_directory: str | Path,
    *,
    input_usd_per_million_tokens: Decimal = Decimal("2.50"),
    output_usd_per_million_tokens: Decimal = Decimal("15"),
    usd_per_web_search_call: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    """Build a deterministic summary for one run directory."""

    run_dir = Path(run_directory).expanduser().resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")
    for rate_name, rate in (
        ("input_usd_per_million_tokens", input_usd_per_million_tokens),
        ("output_usd_per_million_tokens", output_usd_per_million_tokens),
        ("usd_per_web_search_call", usd_per_web_search_call),
    ):
        if rate < 0:
            raise ValueError(f"{rate_name} must be non-negative")

    warnings: list[str] = []
    used_artifacts: set[str] = set()
    manifest_path = run_dir / "manifest.json"
    manifest = (
        _read_json(manifest_path, warnings) if manifest_path.is_file() else {}
    )
    if manifest:
        used_artifacts.add(manifest_path.name)
    else:
        warnings.append("manifest.json is absent or unusable")

    rows = _load_assessments(run_dir, warnings, used_artifacts)
    _load_supplemental_analyst_data(
        run_dir, rows, warnings, used_artifacts
    )
    conditions = _condition_order(manifest, rows)
    raw_metrics = _load_raw_metrics(
        run_dir, conditions, warnings, used_artifacts
    )
    _enrich_rows_with_raw(rows, raw_metrics)
    _recompute_final_output_exposure(rows)

    acquisition_path = run_dir / "acquisition_gate.json"
    acquisition = (
        _read_json(acquisition_path, warnings)
        if acquisition_path.is_file()
        else None
    )
    if acquisition is not None:
        used_artifacts.add(acquisition_path.name)
    else:
        warnings.append("acquisition_gate.json is absent")

    error_rows, raw_error_files = _load_errors(
        run_dir, warnings, used_artifacts
    )
    usage, cost = _usage_and_cost_summary(
        rows,
        manifest,
        error_rows,
        raw_error_files,
        input_usd_per_million_tokens,
        output_usd_per_million_tokens,
        usd_per_web_search_call,
    )
    if cost["unknown_paid_failure_usage_count"]:
        warnings.append(
            "Estimated API cost is a lower bound because usage for "
            f"{cost['unknown_paid_failure_usage_count']} paid failed call(s) is unavailable"
        )
    return {
        "summary_schema_version": SUMMARY_SCHEMA_VERSION,
        "run": {
            "run_directory_name": run_dir.name,
            "run_id": _text(manifest.get("run_id")) or run_dir.name,
            "experiment_name": _text(manifest.get("experiment_name")),
            "scope": _text(manifest.get("scope")),
            "backend": _text(manifest.get("backend")),
            "model": _text(manifest.get("model")),
            "as_of_date": _text(manifest.get("as_of_date")),
            "run_started_at_utc": _text(manifest.get("run_started_at_utc")),
            "created_at_utc": _text(manifest.get("created_at_utc")),
            "symbols": sorted(
                {row.get("symbol") for row in rows if row.get("symbol")}
            ),
            "conditions": conditions,
        },
        "acquisition_gate": _acquisition_summary(acquisition),
        "completion": _completion_summary(
            manifest, len(rows), len(error_rows), raw_error_files
        ),
        "ratings": _ratings_summary(rows, conditions),
        "quality": _quality_summary(rows, conditions),
        "stability": _stability_summary(rows),
        "condition_transitions": _transition_summary(
            rows, conditions, run_dir, warnings, used_artifacts
        ),
        "analyst_comparison": _analyst_summary(rows, conditions),
        "analyst_exposure_audit": _analyst_exposure_summary(
            rows, conditions
        ),
        "web_usage": _web_summary(rows, conditions),
        "token_usage": usage,
        "estimated_api_cost": cost,
        "errors": _error_summary(
            error_rows, raw_error_files, manifest
        ),
        "provenance": {
            "artifacts_used": sorted(used_artifacts),
            "warnings": sorted(set(warnings)),
            "credential_files_read": [],
        },
    }


def _decimal_argument(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"Invalid decimal amount: {value}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("Rates must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print a deterministic JSON summary of one pilot run. "
            "No environment or credential files are read."
        )
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument(
        "--input-usd-per-million-tokens",
        type=_decimal_argument,
        default=Decimal("2.50"),
    )
    parser.add_argument(
        "--output-usd-per-million-tokens",
        type=_decimal_argument,
        default=Decimal("15"),
    )
    parser.add_argument(
        "--usd-per-web-search-call",
        type=_decimal_argument,
        default=Decimal("0.01"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optionally save the same JSON summary to this path.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print minified rather than indented JSON.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.output and args.output.name.startswith(".env"):
        raise SystemExit("Refusing to write a summary to an environment-file path")
    summary = build_summary(
        args.run_directory,
        input_usd_per_million_tokens=args.input_usd_per_million_tokens,
        output_usd_per_million_tokens=args.output_usd_per_million_tokens,
        usd_per_web_search_call=args.usd_per_web_search_call,
    )
    rendered = json.dumps(
        summary,
        indent=None if args.compact else 2,
        sort_keys=True,
        ensure_ascii=False,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
