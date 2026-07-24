#!/usr/bin/env python3
"""Validate a completed or consolidated pilot without reading credentials."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import product
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlparse

from sp500_ai.llm import (
    ALLOWED_EVIDENCE_ORIGINS_BY_CONDITION,
    ASSESSMENT_SCHEMA,
    EVIDENCE_CATEGORIES,
    EVIDENCE_ORIGINS,
    RATINGS,
    REASON_CODES,
    SUPPLIED_EVIDENCE_ORIGINS,
    WEB_EVIDENCE_ORIGINS,
    _audit_web_actions,
    _extract_urls,
)


WEB_ORIGINS = WEB_EVIDENCE_ORIGINS
SUPPLIED_ORIGINS = SUPPLIED_EVIDENCE_ORIGINS
ALLOWED_ORIGINS_BY_CONDITION = ALLOWED_EVIDENCE_ORIGINS_BY_CONDITION
EXPECTED_PACKET_KEYS = {
    "A_MEMORY_ONLY": {"supplied_information"},
    "B_PRICE_HISTORY": {"price_history_monthly"},
    "C_QUANTITATIVE": {
        "price_history_monthly",
        "technical_and_return_features",
        "fundamentals",
        "spy_market_context",
        "sector_context",
    },
    "D_CURRENT_WEB": {
        "price_history_monthly",
        "technical_and_return_features",
        "fundamentals",
        "spy_market_context",
        "sector_context",
    },
}
ANALYST_PACKET_KEY_NAMES = {
    "analystcount",
    "consensusscore",
    "countnormalizedscore",
    "countnormalizedrating",
    "portalratingyahoo",
    "portalrecommendationkey",
    "recommendationkey",
    "recommendationmean",
    "numberofanalystopinions",
    "strongbuy",
    "strongsell",
    "targethighprice",
    "targetlowprice",
    "targetmeanprice",
    "targetmedianprice",
}
ASSESSMENT_CSV_FIELDS = (
    "symbol",
    "rating",
    "confidence_0_100",
    "evidence_sufficiency",
    "forced_rating_due_to_insufficient_data",
    "reason_codes",
    "thesis",
    "risks",
    "evidence",
    "analyst_opinions_encountered",
    "analyst_opinions_used",
    "limitations",
)
TRACKING_QUERY_KEYS = {
    "_sp",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
PERCENT_ESCAPE = re.compile(r"%([0-9a-fA-F]{2})")
UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path.name} is missing a CSV header")
        return list(reader)


def _parse_bool(value: Any) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Expected a boolean, received {value!r}")


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    parsed = json.loads(str(value or "[]"))
    if not isinstance(parsed, list):
        raise ValueError("Expected a JSON array")
    return parsed


def _normalize_percent_escapes(value: str) -> str:
    encoded = quote(value, safe="/:@-._~!$&'()*+,;=%")

    def replace(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        if character in UNRESERVED:
            return character
        return match.group(0).upper()

    return PERCENT_ESCAPE.sub(replace, encoded)


def _normalized_url(value: str) -> str:
    """Normalize harmless URL differences without merging distinct resources."""

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        return value.strip()
    try:
        port = parsed.port
    except ValueError:
        return value.strip()
    if port and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        host = f"{host}:{port}"
    path = _normalize_percent_escapes(parsed.path or "/").rstrip("/") or "/"
    query_items = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_items), doseq=True)
    suffix = f"?{query}" if query else ""
    return f"{scheme}://{host}{path}{suffix}"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_root(run_dir: Path, retry_manifest: dict[str, Any]) -> Path:
    if retry_manifest and run_dir.parent.name == "retries":
        return run_dir.parent.parent
    return run_dir


def _artifact_path(
    run_dir: Path,
    source_root: Path,
    row: dict[str, str],
    folder: str,
    suffix: str,
) -> Path:
    stem = f"{row['symbol']}_{row['condition']}_r{int(row['repetition'])}"
    if row.get("record_provenance") == "TARGETED_RETRY_SUCCESS":
        return run_dir / folder / f"{stem}{suffix}"
    candidate = source_root / folder / f"{stem}{suffix}"
    if candidate.exists():
        return candidate
    return run_dir / folder / f"{stem}{suffix}"


def _record_key(value: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(value.get("symbol") or ""),
        str(value.get("condition") or ""),
        int(value.get("repetition") or 0),
    )


def _parse_prompt(prompt: str) -> tuple[dict[str, Any], dict[str, Any]]:
    marker = "COMPANY IDENTITY\n"
    packet_marker = "\n\nCONDITION DATA PACKET\n"
    _, found, remainder = prompt.partition(marker)
    if not found:
        raise ValueError("COMPANY IDENTITY section is missing")
    identity_text, found, packet_text = remainder.partition(packet_marker)
    if not found:
        raise ValueError("CONDITION DATA PACKET section is missing")
    identity = json.loads(identity_text)
    packet = json.loads(packet_text)
    if not isinstance(identity, dict) or not isinstance(packet, dict):
        raise ValueError("Prompt identity and packet sections must be JSON objects")
    return identity, packet


def _analyst_packet_keys(value: Any) -> set[str]:
    matched: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if (
                    normalized in ANALYST_PACKET_KEY_NAMES
                    or normalized.startswith("pricetarget")
                ):
                    matched.add(str(key))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return matched


def _csv_json_equal(csv_value: Any, json_value: Any) -> bool:
    text = "" if csv_value is None else str(csv_value).strip()
    if json_value is None:
        return text.lower() in {"", "nan", "<na>", "none", "null"}
    if isinstance(json_value, bool):
        try:
            return _parse_bool(text) is json_value
        except ValueError:
            return False
    if isinstance(json_value, (int, float)) and not isinstance(json_value, bool):
        try:
            return Decimal(text) == Decimal(str(json_value))
        except (InvalidOperation, ValueError):
            return False
    if isinstance(json_value, (list, dict)):
        try:
            return json.loads(text) == json_value
        except (json.JSONDecodeError, TypeError):
            return False
    return text == str(json_value)


def _assessment_schema_errors(assessment: Any) -> list[str]:
    if not isinstance(assessment, dict):
        return ["assessment is not a JSON object"]
    errors: list[str] = []
    required = set(ASSESSMENT_SCHEMA["required"])
    allowed = set(ASSESSMENT_SCHEMA["properties"])
    missing = sorted(required - set(assessment))
    extras = sorted(set(assessment) - allowed)
    if missing:
        errors.append(f"missing fields={missing}")
    if extras:
        errors.append(f"unexpected fields={extras}")
    if missing:
        return errors

    if not isinstance(assessment["symbol"], str) or not assessment["symbol"]:
        errors.append("symbol must be a non-empty string")
    try:
        date.fromisoformat(assessment["as_of"])
    except (TypeError, ValueError):
        errors.append("as_of must be an ISO calendar date")
    horizon = assessment["horizon_months"]
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        errors.append("horizon_months must be a positive integer")
    if assessment["rating"] not in RATINGS:
        errors.append(f"rating must be one of {RATINGS}")
    confidence = assessment["confidence_0_100"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int)
        or not 0 <= confidence <= 100
    ):
        errors.append("confidence_0_100 must be an integer from 0 to 100")
    if assessment["evidence_sufficiency"] not in {"LOW", "MEDIUM", "HIGH"}:
        errors.append("evidence_sufficiency must be LOW, MEDIUM, or HIGH")
    if not isinstance(assessment["forced_rating_due_to_insufficient_data"], bool):
        errors.append("forced_rating_due_to_insufficient_data must be boolean")
    if not isinstance(assessment["analyst_opinions_encountered"], bool):
        errors.append("analyst_opinions_encountered must be boolean")
    if not isinstance(assessment["analyst_opinions_used"], bool):
        errors.append("analyst_opinions_used must be boolean")

    reason_codes = assessment["reason_codes"]
    if (
        not isinstance(reason_codes, list)
        or not 1 <= len(reason_codes) <= 5
        or any(code not in REASON_CODES for code in reason_codes)
    ):
        errors.append("reason_codes must contain 1-5 standardized codes")
    for field in ("thesis", "risks"):
        values = assessment[field]
        if (
            not isinstance(values, list)
            or len(values) != 3
            or any(not isinstance(item, str) or not item for item in values)
        ):
            errors.append(f"{field} must contain exactly three non-empty strings")
    limitations = assessment["limitations"]
    if not isinstance(limitations, list) or any(
        not isinstance(item, str) for item in limitations
    ):
        errors.append("limitations must be an array of strings")

    evidence = assessment["evidence"]
    evidence_required = {
        "claim",
        "category",
        "origin",
        "observation_date",
        "publication_date",
        "source_url",
    }
    if not isinstance(evidence, list) or len(evidence) > 12:
        errors.append("evidence must be an array with at most 12 items")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"evidence[{index}] is not an object")
                continue
            if set(item) != evidence_required:
                errors.append(f"evidence[{index}] has an invalid field set")
                continue
            if not isinstance(item["claim"], str) or not item["claim"]:
                errors.append(f"evidence[{index}].claim must be non-empty")
            if item["category"] not in EVIDENCE_CATEGORIES:
                errors.append(f"evidence[{index}].category is invalid")
            if item["origin"] not in EVIDENCE_ORIGINS:
                errors.append(f"evidence[{index}].origin is invalid")
            for field in ("observation_date", "publication_date", "source_url"):
                if item[field] is not None and not isinstance(item[field], str):
                    errors.append(f"evidence[{index}].{field} must be string or null")
    return errors


def _append_once(values: list[str], message: str) -> None:
    if message not in values:
        values.append(message)


def _validate_retry_lineage(
    run_dir: Path,
    source_root: Path,
    manifest: dict[str, Any],
    retry_manifest: dict[str, Any],
    rows: list[dict[str, str]],
    hard_failures: list[str],
) -> None:
    if not retry_manifest:
        return
    if run_dir.parent.name != "retries":
        hard_failures.append("Retry directory is not nested under an original run's retries/")
        return
    if retry_manifest.get("original_run_directory") != source_root.name:
        hard_failures.append("Retry manifest points to a different original run directory")
    if retry_manifest.get("retry_id") != manifest.get("retry_id"):
        hard_failures.append("Retry ID differs between manifest files")
    if retry_manifest.get("consolidated_run_id") != manifest.get("run_id"):
        hard_failures.append("Consolidated run ID differs between manifest files")
    if retry_manifest.get("original_artifacts_overwritten") is not False:
        hard_failures.append("Retry manifest does not preserve original artifacts")
    if manifest.get("original_artifacts_overwritten") is not False:
        hard_failures.append("Consolidated manifest does not preserve original artifacts")

    source_hashes = manifest.get("source_artifact_sha256")
    if not isinstance(source_hashes, dict) or not source_hashes:
        hard_failures.append("Consolidated manifest lacks source artifact hashes")
    else:
        for relative, expected_hash in sorted(source_hashes.items()):
            source_path = source_root / relative
            if not source_path.is_file():
                hard_failures.append(f"Missing hashed source artifact: {relative}")
            elif _sha256_path(source_path) != expected_hash:
                hard_failures.append(f"Source artifact hash mismatch: {relative}")

    requested = retry_manifest.get("requested_cells")
    if not isinstance(requested, list):
        hard_failures.append("Retry manifest requested_cells is not an array")
        return
    requested_by_key: dict[tuple[str, str, int], dict[str, Any]] = {}
    for item in requested:
        if not isinstance(item, dict):
            hard_failures.append("Retry manifest contains a non-object requested cell")
            continue
        key = _record_key(item)
        if key in requested_by_key:
            hard_failures.append(f"Duplicate retry request: {key}")
        requested_by_key[key] = item
    targeted_by_key = {
        _record_key(row): row
        for row in rows
        if row.get("record_provenance") == "TARGETED_RETRY_SUCCESS"
    }
    if set(requested_by_key) != set(targeted_by_key):
        hard_failures.append("Retry requested-cell set differs from targeted success rows")
    requested_count = len(requested_by_key)
    if int(retry_manifest.get("successful_cells") or 0) != len(targeted_by_key):
        hard_failures.append("Retry successful-cell count is inconsistent")
    if int(retry_manifest.get("failed_cells") or 0) != 0:
        hard_failures.append("Consolidated retry still reports failed cells")
    if int(manifest.get("targeted_retry_requested") or 0) != requested_count:
        hard_failures.append("Consolidated manifest retry-request count is inconsistent")
    if int(manifest.get("targeted_retry_succeeded") or 0) != len(targeted_by_key):
        hard_failures.append("Consolidated manifest retry-success count is inconsistent")
    if int(manifest.get("targeted_retry_failed") or 0) != 0:
        hard_failures.append("Consolidated manifest still reports retry failures")

    original_errors_path = source_root / "assessment_errors.csv"
    if original_errors_path.is_file():
        original_error_keys = {
            _record_key(row) for row in _read_csv(original_errors_path)
        }
        if not set(requested_by_key).issubset(original_error_keys):
            hard_failures.append("Retry request was not present in original failures")

    retried_hashes = manifest.get("retried_prompt_sha256")
    if not isinstance(retried_hashes, dict):
        hard_failures.append("Consolidated manifest lacks retried prompt hashes")
        retried_hashes = {}
    for key, request in requested_by_key.items():
        symbol, condition, repetition = key
        stem = f"{symbol}_{condition}_r{repetition}"
        source_prompt = source_root / "prompts" / f"{stem}.txt"
        retry_prompt = run_dir / "prompts" / f"{stem}.txt"
        if not source_prompt.is_file() or not retry_prompt.is_file():
            hard_failures.append(f"Retry prompt lineage is incomplete: {stem}")
            continue
        source_bytes = source_prompt.read_bytes()
        retry_bytes = retry_prompt.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        if source_bytes != retry_bytes:
            hard_failures.append(f"Retry prompt differs from archived original: {stem}")
        if request.get("prompt_sha256") != source_hash:
            hard_failures.append(f"Retry request prompt hash mismatch: {stem}")
        if retried_hashes.get(stem) != source_hash:
            hard_failures.append(f"Consolidated retried prompt hash mismatch: {stem}")
        row = targeted_by_key.get(key)
        if row:
            if row.get("prompt_sha256") != source_hash:
                hard_failures.append(f"Targeted row prompt hash mismatch: {stem}")
            if str(row.get("retry_id") or "") != str(
                retry_manifest.get("retry_id") or ""
            ):
                hard_failures.append(f"Targeted row retry ID mismatch: {stem}")
            if int(row.get("retry_attempt") or 0) != 1:
                hard_failures.append(f"Targeted row retry attempt is not one: {stem}")
            if int(row.get("original_failure_execution_order") or 0) != int(
                request.get("original_execution_order") or 0
            ):
                hard_failures.append(
                    f"Targeted row original execution order mismatch: {stem}"
                )
            if row.get("original_failure_error_type") != request.get(
                "original_error_type"
            ):
                hard_failures.append(f"Targeted row original error type mismatch: {stem}")

    attempts_path = run_dir / "retry_attempts.csv"
    if not attempts_path.is_file():
        hard_failures.append("Missing retry_attempts.csv")
    else:
        attempts = _read_csv(attempts_path)
        successful_attempt_keys = {
            _record_key(row)
            for row in attempts
            if str(row.get("status") or "").upper() == "SUCCESS"
        }
        if successful_attempt_keys != set(targeted_by_key):
            hard_failures.append("Successful retry attempts differ from targeted rows")


def _validate_artifacts(run_dir: Path) -> dict[str, Any]:
    hard_failures: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    required = (
        "manifest.json",
        "acquisition_gate.json",
        "recommendations.csv",
        "recommendations.jsonl",
        "assessment_errors.csv",
    )
    for name in required:
        if not (run_dir / name).is_file():
            hard_failures.append(f"Missing required artifact: {name}")
    if hard_failures:
        return {
            "status": "FAIL",
            "hard_failures": hard_failures,
            "warnings": warnings,
            "metrics": metrics,
            "credential_files_read": [],
        }

    manifest = _read_json(run_dir / "manifest.json")
    gate = _read_json(run_dir / "acquisition_gate.json")
    retry_manifest = (
        _read_json(run_dir / "retry_manifest.json")
        if (run_dir / "retry_manifest.json").is_file()
        else {}
    )
    source_root = _source_root(run_dir, retry_manifest)
    rows = _read_csv(run_dir / "recommendations.csv")
    errors = _read_csv(run_dir / "assessment_errors.csv")
    jsonl_rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        (run_dir / "recommendations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(
                f"recommendations.jsonl line {line_number} is not an object"
            )
        jsonl_rows.append(item)

    planned = int(manifest.get("planned_assessments", 0))
    completed = int(manifest.get("completed_assessments", 0))
    failed = int(manifest.get("failed_assessments", 0))
    not_attempted = int(manifest.get("not_attempted_assessments", 0))
    if gate.get("status") != "PASS":
        hard_failures.append("Acquisition gate did not pass")
    if len(rows) != planned or completed != planned:
        hard_failures.append(
            f"Assessment count mismatch: rows={len(rows)}, completed={completed}, planned={planned}"
        )
    if failed or not_attempted or errors:
        hard_failures.append(
            f"Unresolved execution failures: failed={failed}, not_attempted={not_attempted}, error_rows={len(errors)}"
        )

    symbols = manifest.get("symbols")
    conditions = manifest.get("conditions")
    repetitions = int(manifest.get("repetitions", 0))
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(symbol, str) or not symbol for symbol in symbols)
        or len(set(symbols)) != len(symbols)
    ):
        hard_failures.append("Manifest symbols must be a unique, non-empty string array")
        symbols = []
    if (
        not isinstance(conditions, list)
        or not conditions
        or any(
            not isinstance(condition, str) or condition not in EXPECTED_PACKET_KEYS
            for condition in conditions
        )
        or len(set(conditions)) != len(conditions)
    ):
        hard_failures.append("Manifest conditions are missing, duplicated, or unknown")
        conditions = []
    if repetitions <= 0:
        hard_failures.append("Manifest repetitions must be positive")
    expected_keys = {
        (symbol, condition, repetition)
        for symbol, condition, repetition in product(
            symbols,
            conditions,
            range(1, repetitions + 1),
        )
    }
    if planned != len(expected_keys):
        hard_failures.append(
            f"Planned count is not symbols×conditions×repetitions: planned={planned}, expected={len(expected_keys)}"
        )

    keys = [_record_key(row) for row in rows]
    if len(set(keys)) != len(keys):
        hard_failures.append("Duplicate CSV symbol-condition-repetition cells detected")
    actual_keys = set(keys)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unexpected = sorted(actual_keys - expected_keys)
        hard_failures.append(
            f"CSV cell set differs from manifest universe: missing={missing}, unexpected={unexpected}"
        )

    jsonl_keys = [_record_key(item) for item in jsonl_rows]
    if len(set(jsonl_keys)) != len(jsonl_keys):
        hard_failures.append("Duplicate JSONL symbol-condition-repetition cells detected")
    if set(jsonl_keys) != actual_keys or len(jsonl_rows) != len(rows):
        hard_failures.append("JSONL and CSV cell sets or row counts differ")
    outer_by_key = {_record_key(item): item for item in jsonl_rows}

    cutoff = date.fromisoformat(str(manifest["as_of_date"]))
    evidence_items = 0
    future_dates = 0
    invalid_dates = 0
    semantic_date_gaps = 0
    cited_urls = 0
    exact_trace_matches = 0
    normalized_trace_matches = 0
    unmatched_urls: set[str] = set()
    origin_counts: Counter[str] = Counter()
    origins_by_condition: dict[str, Counter[str]] = defaultdict(Counter)
    prompt_count = 0
    raw_count = 0
    prompt_hash_mismatches = 0
    packet_hash_mismatches = 0
    identity_mismatches = 0
    csv_jsonl_mismatches = 0
    raw_metadata_mismatches = 0
    schema_invalid_cells = 0
    packet_leakage: dict[str, list[str]] = {}
    packet_hashes: dict[tuple[str, str], set[str]] = defaultdict(set)
    horizons: set[int] = set()
    web_by_condition: dict[str, int] = defaultdict(int)

    for row in rows:
        key = _record_key(row)
        cell = "/".join(map(str, key))
        outer = outer_by_key.get(key)
        if outer is None:
            continue
        for field, csv_value in row.items():
            if field not in outer or not _csv_json_equal(csv_value, outer.get(field)):
                csv_jsonl_mismatches += 1
                break
        assessment = outer.get("assessment")
        schema_errors = _assessment_schema_errors(assessment)
        if schema_errors:
            schema_invalid_cells += 1
            hard_failures.append(
                f"Invalid assessment schema for {cell}: {'; '.join(schema_errors)}"
            )
            assessment = assessment if isinstance(assessment, dict) else {}
        for field in ASSESSMENT_CSV_FIELDS:
            if field not in assessment or not _csv_json_equal(
                row.get(field), assessment.get(field)
            ):
                csv_jsonl_mismatches += 1
                break
        if (
            assessment.get("symbol") != row["symbol"]
            or assessment.get("as_of") != manifest["as_of_date"]
        ):
            identity_mismatches += 1
        horizon = assessment.get("horizon_months")
        if isinstance(horizon, int) and not isinstance(horizon, bool):
            horizons.add(horizon)

        prompt_path = _artifact_path(run_dir, source_root, row, "prompts", ".txt")
        raw_path = _artifact_path(
            run_dir,
            source_root,
            row,
            "raw_responses",
            ".json",
        )
        packet: dict[str, Any] | None = None
        if not prompt_path.is_file():
            hard_failures.append(f"Missing prompt: {prompt_path.name}")
        else:
            prompt_count += 1
            prompt = prompt_path.read_text(encoding="utf-8")
            if _sha256_text(prompt) != row.get("prompt_sha256"):
                prompt_hash_mismatches += 1
            try:
                identity, packet = _parse_prompt(prompt)
            except (json.JSONDecodeError, ValueError) as exc:
                hard_failures.append(f"Invalid prompt sections for {cell}: {exc}")
            else:
                expected_identity = {
                    "symbol": row["symbol"],
                    "company": row.get("company"),
                    "sector": row.get("sector"),
                    "as_of_date": manifest["as_of_date"],
                    "horizon_months": horizon,
                    "prompt_version": row.get("prompt_version"),
                }
                if any(
                    identity.get(field) != expected
                    for field, expected in expected_identity.items()
                ):
                    identity_mismatches += 1
                computed_packet_hash = _sha256_json(packet)
                packet_hashes[(row["symbol"], row["condition"])].add(
                    computed_packet_hash
                )
                if computed_packet_hash != row.get("packet_sha256"):
                    packet_hash_mismatches += 1
                expected_packet_keys = EXPECTED_PACKET_KEYS.get(row["condition"])
                if expected_packet_keys is None or set(packet) != expected_packet_keys:
                    hard_failures.append(
                        f"Condition packet shape is invalid for {cell}"
                    )
                analyst_keys = sorted(_analyst_packet_keys(packet))
                if analyst_keys:
                    packet_leakage[cell] = analyst_keys

        retrieved_urls: set[str] = set()
        raw: dict[str, Any] | None = None
        if not raw_path.is_file():
            hard_failures.append(f"Missing raw response: {raw_path.name}")
        else:
            try:
                raw = _read_json(raw_path)
            except (json.JSONDecodeError, ValueError) as exc:
                hard_failures.append(f"Invalid raw response for {cell}: {exc}")
            else:
                raw_count += 1
                retrieved_urls = set(_extract_urls(raw))
                csv_urls = set(_json_list(row.get("retrieved_source_urls")))
                if retrieved_urls != csv_urls:
                    raw_metadata_mismatches += 1
                raw_audit = _audit_web_actions(raw)
                for field in (
                    "web_search_call_count",
                    "web_search_source_count",
                    "analyst_terms_detected_in_web_actions",
                    "analyst_web_action_matched_terms",
                ):
                    if row.get(field) not in (None, "") and not _csv_json_equal(
                        row.get(field),
                        raw_audit.get(field),
                    ):
                        raw_metadata_mismatches += 1
                if str(row.get("backend") or "").upper().startswith("OPENAI"):
                    usage = raw.get("usage") or {}
                    raw_fields = {
                        "response_id": raw.get("id"),
                        "model": raw.get("model"),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                    }
                    for field, raw_value in raw_fields.items():
                        if not _csv_json_equal(row.get(field), raw_value):
                            raw_metadata_mismatches += 1
        normalized_retrieved = {_normalized_url(url) for url in retrieved_urls}

        evidence = assessment.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        allowed_origins = ALLOWED_ORIGINS_BY_CONDITION.get(row["condition"], set())
        for item in evidence:
            if not isinstance(item, dict):
                continue
            evidence_items += 1
            origin = str(item.get("origin") or "")
            origin_counts[origin] += 1
            origins_by_condition[row["condition"]][origin] += 1
            if origin not in allowed_origins:
                hard_failures.append(
                    f"Evidence origin {origin!r} is not allowed in {cell}"
                )
            if origin in WEB_ORIGINS and (
                not item.get("publication_date") or not item.get("source_url")
            ):
                semantic_date_gaps += 1
            if origin in SUPPLIED_ORIGINS and not item.get("observation_date"):
                semantic_date_gaps += 1
            for field in ("observation_date", "publication_date"):
                value = item.get(field)
                if not value:
                    continue
                try:
                    observed = date.fromisoformat(str(value))
                except ValueError:
                    invalid_dates += 1
                    continue
                if observed > cutoff:
                    future_dates += 1
            source_url = item.get("source_url")
            if source_url:
                parsed_url = urlparse(str(source_url))
                if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                    hard_failures.append(f"Invalid evidence source URL in {cell}")
                    continue
                cited_urls += 1
                if source_url in retrieved_urls:
                    exact_trace_matches += 1
                elif _normalized_url(str(source_url)) in normalized_retrieved:
                    normalized_trace_matches += 1
                else:
                    unmatched_urls.add(str(source_url))

        try:
            calls = int(row.get("web_search_call_count") or 0)
            enabled = _parse_bool(row.get("web_search_enabled"))
        except ValueError as exc:
            hard_failures.append(f"Invalid web metadata for {cell}: {exc}")
            calls = 0
            enabled = False
        web_by_condition[row["condition"]] += calls
        if row["condition"] == "D_CURRENT_WEB":
            if not enabled:
                hard_failures.append(
                    f"Condition D lacked web enablement: {row['symbol']}/r{row['repetition']}"
                )
            if (
                str(row.get("backend") or "").upper().startswith("OPENAI")
                and calls <= 0
            ):
                hard_failures.append(
                    f"Condition D made no web-search call: {row['symbol']}/r{row['repetition']}"
                )
        elif enabled or calls:
            hard_failures.append(
                f"Non-D condition used web: {row['symbol']}/{row['condition']}/r{row['repetition']}"
            )

    if len(horizons) != 1:
        hard_failures.append(f"Assessment horizons are inconsistent: {sorted(horizons)}")
    manifest_horizon = manifest.get("horizon_months")
    if manifest_horizon is not None and horizons != {int(manifest_horizon)}:
        hard_failures.append("Assessment horizon differs from manifest")
    if identity_mismatches:
        hard_failures.append(f"Identity/date mismatches: {identity_mismatches}")
    if csv_jsonl_mismatches:
        hard_failures.append(
            f"CSV/JSONL assessment or metadata mismatches: {csv_jsonl_mismatches}"
        )
    if schema_invalid_cells:
        _append_once(
            hard_failures,
            f"Assessment schema failures: {schema_invalid_cells} cell(s)",
        )
    if prompt_hash_mismatches:
        hard_failures.append(f"Prompt hash mismatches: {prompt_hash_mismatches}")
    if packet_hash_mismatches:
        hard_failures.append(f"Packet hash mismatches: {packet_hash_mismatches}")
    if packet_leakage:
        hard_failures.append(
            "Analyst benchmark keys appeared in supplied packets: "
            + ", ".join(
                f"{cell}={keys}" for cell, keys in sorted(packet_leakage.items())
            )
        )
    if raw_metadata_mismatches:
        hard_failures.append(
            f"Raw-response/CSV metadata mismatches: {raw_metadata_mismatches}"
        )
    if invalid_dates or future_dates or semantic_date_gaps:
        hard_failures.append(
            "Evidence-date validation failed: "
            f"invalid={invalid_dates}, future={future_dates}, semantic_gaps={semantic_date_gaps}"
        )

    cd_identity: dict[str, bool] = {}
    for symbol in symbols:
        c_hashes = packet_hashes.get((symbol, "C_QUANTITATIVE"), set())
        d_hashes = packet_hashes.get((symbol, "D_CURRENT_WEB"), set())
        cd_identity[symbol] = (
            bool(c_hashes)
            and c_hashes == d_hashes
            and len(c_hashes) == 1
        )
    if {"C_QUANTITATIVE", "D_CURRENT_WEB"}.issubset(set(conditions)) and not all(
        cd_identity.values()
    ):
        hard_failures.append("C and D supplied packets differ for at least one stock")

    _validate_retry_lineage(
        run_dir,
        source_root,
        manifest,
        retry_manifest,
        rows,
        hard_failures,
    )

    if unmatched_urls:
        warnings.append(
            f"{len(unmatched_urls)} unique cited URLs require manual trace review"
        )
    if int(manifest.get("unknown_paid_failure_usage_count", 0)):
        warnings.append("API cost is a lower bound because failed-call usage is unknown")

    provenance_counts = Counter(
        row.get("record_provenance") or "UNSPECIFIED" for row in rows
    )
    metrics.update(
        {
            "planned_assessments": planned,
            "assessment_rows": len(rows),
            "unique_cells": len(set(keys)),
            "expected_cells": len(expected_keys),
            "stock_condition_groups": len(
                {(symbol, condition) for symbol, condition, _ in keys}
            ),
            "expected_repetitions": repetitions,
            "prompt_files_validated": prompt_count,
            "raw_responses_validated": raw_count,
            "evidence_items": evidence_items,
            "evidence_origin_counts": dict(sorted(origin_counts.items())),
            "evidence_origin_counts_by_condition": {
                condition: dict(sorted(counts.items()))
                for condition, counts in sorted(origins_by_condition.items())
            },
            "invalid_evidence_dates": invalid_dates,
            "post_cutoff_evidence_dates": future_dates,
            "evidence_date_semantic_gaps": semantic_date_gaps,
            "cited_url_instances": cited_urls,
            "exact_source_trace_matches": exact_trace_matches,
            "normalized_source_trace_matches": normalized_trace_matches,
            "unmatched_unique_source_urls": sorted(unmatched_urls),
            "web_calls_by_condition": dict(sorted(web_by_condition.items())),
            "c_d_packet_identity_by_symbol": cd_identity,
            "record_provenance_counts": dict(sorted(provenance_counts.items())),
        }
    )
    return {
        "status": (
            "FAIL"
            if hard_failures
            else ("PASS_WITH_WARNINGS" if warnings else "PASS")
        ),
        "hard_failures": hard_failures,
        "warnings": warnings,
        "metrics": metrics,
        "credential_files_read": [],
    }


def validate(run_directory: str | Path) -> dict[str, Any]:
    run_dir = Path(run_directory).expanduser().resolve()
    try:
        return _validate_artifacts(run_dir)
    except (
        csv.Error,
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        return {
            "status": "FAIL",
            "hard_failures": [
                f"Artifact parsing failed: {type(exc).__name__}: {exc}"
            ],
            "warnings": [],
            "metrics": {},
            "credential_files_read": [],
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a pilot artifact directory without loading .env"
    )
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.run_directory)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if result["hard_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
